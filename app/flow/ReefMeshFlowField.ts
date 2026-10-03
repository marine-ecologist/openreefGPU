import * as THREE from 'three';
import { MeshBVH, SAH } from 'three-mesh-bvh';

import { FlowField, type SurfaceSample } from './FlowField';

export type ReefFlowOptions = {
  direction: number;
  speed: number;
  wakeStrength: number;
  surfaceFollowing: number;
  gridResolution?: number;
};

const UP = new THREE.Vector3(0, 0, -1);
const TAU = Math.PI * 2;

const clamp01 = (value: number) => Math.max(0, Math.min(1, value));

/**
 * Mesh-derived pseudo-flow. The expensive triangle queries happen once while
 * constructing a regular terrain cache; per-particle samples are constant time.
 */
export class ReefMeshFlowField extends FlowField {
  readonly bounds: THREE.Box3;

  private readonly resolution: number;
  private readonly heights: Float32Array;
  private readonly normals: Float32Array;
  private readonly valid: Uint8Array;
  private readonly size = new THREE.Vector3();
  private readonly ray = new THREE.Ray();
  private readonly baseDirection = new THREE.Vector3();
  private readonly surfaceNormal = new THREE.Vector3();
  private readonly tangentVelocity = new THREE.Vector3();
  private readonly currentSurface: SurfaceSample = {
    height: 0,
    normalX: 0,
    normalY: 0,
    normalZ: -1,
    relief: 0,
  };
  private readonly aheadSurface: SurfaceSample = { ...this.currentSurface };
  private readonly options: ReefFlowOptions;
  private minSurface = Number.POSITIVE_INFINITY;
  private maxSurface = Number.NEGATIVE_INFINITY;
  private referenceSpeed = 0;

  constructor(sourceGeometry: THREE.BufferGeometry, options: ReefFlowOptions) {
    super();
    this.options = { ...options };
    this.resolution = options.gridResolution ?? 144;
    this.heights = new Float32Array(this.resolution * this.resolution);
    this.normals = new Float32Array(this.resolution * this.resolution * 3);
    this.valid = new Uint8Array(this.resolution * this.resolution);

    const geometry = sourceGeometry.clone();
    if (!geometry.getAttribute('normal')) geometry.computeVertexNormals();
    geometry.computeBoundingBox();
    this.bounds = geometry.boundingBox?.clone() ?? new THREE.Box3();
    this.bounds.getSize(this.size);

    const bvh = new MeshBVH(geometry, {
      strategy: SAH,
      targetLeafSize: 12,
      setBoundingBox: true,
    });
    this.buildTerrainCache(bvh);
    geometry.dispose();
    this.updateDirection();
  }

  setDirection(degrees: number) {
    this.options.direction = ((degrees % 360) + 360) % 360;
    this.updateDirection();
  }

  setSpeed(speed: number) {
    this.options.speed = clamp01(speed);
    this.updateReferenceSpeed();
  }

  setWakeStrength(strength: number) {
    this.options.wakeStrength = clamp01(strength);
  }

  setSurfaceFollowing(strength: number) {
    this.options.surfaceFollowing = clamp01(strength);
  }

  getReferenceSpeed() {
    return this.referenceSpeed;
  }

  getClearanceRange() {
    const scale = Math.max(this.size.x, this.size.y, 0.001);
    return { min: scale * 0.008, max: scale * 0.12 };
  }

  sample(position: THREE.Vector3, time: number, target = new THREE.Vector3()) {
    target.copy(this.baseDirection).multiplyScalar(this.referenceSpeed);
    if (this.referenceSpeed <= 0) return target.set(0, 0, 0);

    const surface = this.surfaceAt(position.x, position.y, this.currentSurface);
    if (!surface) return target;

    const lookAhead = Math.max(this.size.x, this.size.y) * 0.025;
    const ahead = this.surfaceAt(
      position.x + this.baseDirection.x * lookAhead,
      position.y + this.baseDirection.y * lookAhead,
      this.aheadSurface,
    );

    const normal = this.surfaceNormal.set(
      surface.normalX,
      surface.normalY,
      surface.normalZ,
    );
    const normalDot = target.dot(normal);
    this.tangentVelocity.copy(target).addScaledVector(normal, -normalDot);
    target.lerp(this.tangentVelocity, this.options.surfaceFollowing * 0.82);

    // Exposed crests accelerate the flow in relative (not physical) units.
    const exposureBoost = 1 + surface.relief * 0.42;
    target.multiplyScalar(exposureBoost);

    if (ahead) {
      // Positive descent follows a crest when +Z is down in this reconstruction.
      const descent = Math.max(0, (ahead.height - surface.height) / lookAhead);
      const wake = clamp01(descent * Math.max(this.size.x, this.size.y) * 1.35);
      const wakeAmount = wake * this.options.wakeStrength;
      target.multiplyScalar(1 - wakeAmount * 0.58);

      if (wakeAmount > 0.001) {
        const along =
          position.x * this.baseDirection.x + position.y * this.baseDirection.y;
        const cross =
          -position.x * this.baseDirection.y +
          position.y * this.baseDirection.x;
        const wavelength = Math.max(this.size.x, this.size.y) * 0.12;
        const phase =
          (along / wavelength) * TAU +
          (cross / wavelength) * Math.PI -
          time * (1.2 + this.options.speed * 1.8);
        const vortex =
          Math.sin(phase) * this.referenceSpeed * wakeAmount * 0.52;
        target.x += -this.baseDirection.y * vortex;
        target.y += this.baseDirection.x * vortex;
        target.z +=
          Math.cos(phase * 0.5) * this.referenceSpeed * wakeAmount * 0.09;
      }
    }

    // Softly return particles to a terrain-following band without imposing a
    // fixed water depth or any physical units.
    const clearance = Math.max(0, surface.height - position.z);
    const desired = this.getClearanceRange().min * 2.5;
    target.z += (clearance - desired) * this.options.surfaceFollowing * 0.36;
    return target;
  }

  surfaceAt(
    x: number,
    y: number,
    target?: SurfaceSample,
  ): SurfaceSample | null {
    const fx =
      ((x - this.bounds.min.x) / Math.max(this.size.x, 1e-6)) *
      (this.resolution - 1);
    const fy =
      ((y - this.bounds.min.y) / Math.max(this.size.y, 1e-6)) *
      (this.resolution - 1);
    if (
      fx < 0 ||
      fy < 0 ||
      fx > this.resolution - 1 ||
      fy > this.resolution - 1
    )
      return null;

    const x0 = Math.floor(fx);
    const y0 = Math.floor(fy);
    const x1 = Math.min(x0 + 1, this.resolution - 1);
    const y1 = Math.min(y0 + 1, this.resolution - 1);
    const tx = fx - x0;
    const ty = fy - y0;
    const corners = [
      { index: y0 * this.resolution + x0, weight: (1 - tx) * (1 - ty) },
      { index: y0 * this.resolution + x1, weight: tx * (1 - ty) },
      { index: y1 * this.resolution + x0, weight: (1 - tx) * ty },
      { index: y1 * this.resolution + x1, weight: tx * ty },
    ];

    let totalWeight = 0;
    let height = 0;
    let nx = 0;
    let ny = 0;
    let nz = 0;
    for (const corner of corners) {
      if (!this.valid[corner.index]) continue;
      totalWeight += corner.weight;
      height += this.heights[corner.index] * corner.weight;
      nx += this.normals[corner.index * 3] * corner.weight;
      ny += this.normals[corner.index * 3 + 1] * corner.weight;
      nz += this.normals[corner.index * 3 + 2] * corner.weight;
    }
    if (totalWeight <= 1e-6) return null;

    const inverseWeight = 1 / totalWeight;
    height *= inverseWeight;
    nx *= inverseWeight;
    ny *= inverseWeight;
    nz *= inverseWeight;
    const normalLength = Math.hypot(nx, ny, nz) || 1;
    const result = target ?? {
      height: 0,
      normalX: 0,
      normalY: 0,
      normalZ: -1,
      relief: 0,
    };
    result.height = height;
    result.normalX = nx / normalLength;
    result.normalY = ny / normalLength;
    result.normalZ = nz / normalLength;
    result.relief = clamp01(
      (this.maxSurface - height) /
        Math.max(this.maxSurface - this.minSurface, 1e-6),
    );
    return result;
  }

  private updateDirection() {
    const radians = THREE.MathUtils.degToRad(this.options.direction);
    this.baseDirection
      .set(Math.sin(radians), -Math.cos(radians), 0)
      .normalize();
    this.updateReferenceSpeed();
  }

  private updateReferenceSpeed() {
    const domainScale = Math.max(this.size.x, this.size.y, 0.001);
    this.referenceSpeed = domainScale * 0.075 * clamp01(this.options.speed);
  }

  private buildTerrainCache(bvh: MeshBVH) {
    const rayStart = this.bounds.min.z - Math.max(this.size.z, 1) * 0.25;
    const far = Math.max(this.size.z, 1) * 1.5;
    this.ray.direction.set(0, 0, 1);

    for (let yIndex = 0; yIndex < this.resolution; yIndex += 1) {
      const y = THREE.MathUtils.lerp(
        this.bounds.min.y,
        this.bounds.max.y,
        yIndex / (this.resolution - 1),
      );
      for (let xIndex = 0; xIndex < this.resolution; xIndex += 1) {
        const x = THREE.MathUtils.lerp(
          this.bounds.min.x,
          this.bounds.max.x,
          xIndex / (this.resolution - 1),
        );
        this.ray.origin.set(x, y, rayStart);
        const hit = bvh.raycastFirst(this.ray, THREE.DoubleSide, 0, far);
        if (!hit) continue;

        const index = yIndex * this.resolution + xIndex;
        const normal = hit.face?.normal?.clone() ?? UP.clone();
        if (normal.z > 0) normal.multiplyScalar(-1);
        normal.normalize();
        this.valid[index] = 1;
        this.heights[index] = hit.point.z;
        this.normals[index * 3] = normal.x;
        this.normals[index * 3 + 1] = normal.y;
        this.normals[index * 3 + 2] = normal.z;
        this.minSurface = Math.min(this.minSurface, hit.point.z);
        this.maxSurface = Math.max(this.maxSurface, hit.point.z);
      }
    }

    if (!Number.isFinite(this.minSurface)) {
      this.minSurface = this.bounds.min.z;
      this.maxSurface = this.bounds.max.z;
    }
  }
}
