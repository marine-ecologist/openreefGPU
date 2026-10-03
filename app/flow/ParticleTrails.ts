import * as THREE from 'three';

import { trailFragmentShader, trailVertexShader } from './flowShaders';

export class ParticleTrails {
  readonly object: THREE.LineSegments;

  private readonly positions: Float32Array;
  private readonly speeds: Float32Array;
  private readonly alphas: Float32Array;
  private readonly positionAttribute: THREE.BufferAttribute;
  private readonly speedAttribute: THREE.BufferAttribute;

  constructor(
    private readonly particleCount: number,
    private readonly segmentCount: number,
  ) {
    const vertexCount = particleCount * segmentCount * 2;
    this.positions = new Float32Array(vertexCount * 3);
    this.speeds = new Float32Array(vertexCount);
    this.alphas = new Float32Array(vertexCount);
    this.positionAttribute = new THREE.BufferAttribute(
      this.positions,
      3,
    ).setUsage(THREE.DynamicDrawUsage);
    this.speedAttribute = new THREE.BufferAttribute(this.speeds, 1).setUsage(
      THREE.DynamicDrawUsage,
    );
    const alphaAttribute = new THREE.BufferAttribute(this.alphas, 1);

    for (let particle = 0; particle < particleCount; particle += 1) {
      for (let segment = 0; segment < segmentCount; segment += 1) {
        const alpha = Math.pow(1 - segment / segmentCount, 1.45) * 0.82;
        const offset = (particle * segmentCount + segment) * 2;
        this.alphas[offset] = alpha;
        this.alphas[offset + 1] = alpha * 0.88;
      }
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', this.positionAttribute);
    geometry.setAttribute('flowSpeed', this.speedAttribute);
    geometry.setAttribute('trailAlpha', alphaAttribute);
    geometry.boundingSphere = new THREE.Sphere(
      new THREE.Vector3(),
      Number.POSITIVE_INFINITY,
    );

    const material = new THREE.ShaderMaterial({
      vertexShader: trailVertexShader,
      fragmentShader: trailFragmentShader,
      transparent: true,
      depthWrite: false,
      depthTest: true,
      blending: THREE.AdditiveBlending,
      toneMapped: false,
    });
    this.object = new THREE.LineSegments(geometry, material);
    this.object.frustumCulled = false;
    this.object.renderOrder = 4;
  }

  update(
    history: Float32Array,
    speedHistory: Float32Array,
    historyHead: number,
  ) {
    const historySlots = this.segmentCount + 1;
    let vertex = 0;
    for (let particle = 0; particle < this.particleCount; particle += 1) {
      for (let segment = 0; segment < this.segmentCount; segment += 1) {
        const newerSlot = (historyHead - segment + historySlots) % historySlots;
        const olderSlot =
          (historyHead - segment - 1 + historySlots) % historySlots;
        const newer = (newerSlot * this.particleCount + particle) * 3;
        const older = (olderSlot * this.particleCount + particle) * 3;
        const speed = speedHistory[newerSlot * this.particleCount + particle];
        const positionOffset = vertex * 3;

        this.positions[positionOffset] = history[newer];
        this.positions[positionOffset + 1] = history[newer + 1];
        this.positions[positionOffset + 2] = history[newer + 2];
        this.positions[positionOffset + 3] = history[older];
        this.positions[positionOffset + 4] = history[older + 1];
        this.positions[positionOffset + 5] = history[older + 2];
        this.speeds[vertex] = speed;
        this.speeds[vertex + 1] =
          speedHistory[olderSlot * this.particleCount + particle];
        vertex += 2;
      }
    }
    this.positionAttribute.needsUpdate = true;
    this.speedAttribute.needsUpdate = true;
  }

  dispose() {
    this.object.geometry.dispose();
    (this.object.material as THREE.Material).dispose();
  }
}
