import * as THREE from 'three';

import type { ReefMeshFlowField } from './ReefMeshFlowField';

export type SeedMode = 'domain' | 'upstream';

export class FlowSeeds {
  private state = 0x2f6e2b1;
  private readonly center = new THREE.Vector3();
  private readonly size = new THREE.Vector3();

  constructor(private readonly field: ReefMeshFlowField) {
    field.bounds.getCenter(this.center);
    field.bounds.getSize(this.size);
  }

  resetRandom() {
    this.state = 0x2f6e2b1;
  }

  seed(target: THREE.Vector3, directionDegrees: number, mode: SeedMode) {
    const radians = THREE.MathUtils.degToRad(directionDegrees);
    const flowX = Math.sin(radians);
    const flowY = -Math.cos(radians);
    const crossX = -flowY;
    const crossY = flowX;
    const halfAlong =
      Math.abs(flowX) * this.size.x * 0.5 + Math.abs(flowY) * this.size.y * 0.5;
    const halfCross =
      Math.abs(crossX) * this.size.x * 0.5 +
      Math.abs(crossY) * this.size.y * 0.5;
    const clearances = this.field.getClearanceRange();

    for (let attempt = 0; attempt < 18; attempt += 1) {
      const along =
        mode === 'upstream'
          ? -halfAlong + this.random() * halfAlong * 0.08
          : THREE.MathUtils.lerp(-halfAlong, halfAlong, this.random());
      const across = THREE.MathUtils.lerp(-halfCross, halfCross, this.random());
      const x = this.center.x + flowX * along + crossX * across;
      const y = this.center.y + flowY * along + crossY * across;
      const surface = this.field.surfaceAt(x, y);
      if (!surface) continue;

      const clearance = THREE.MathUtils.lerp(
        clearances.min,
        clearances.max,
        this.random(),
      );
      return target.set(x, y, surface.height - clearance);
    }

    const fallback = this.field.surfaceAt(this.center.x, this.center.y);
    return target.set(
      this.center.x,
      this.center.y,
      (fallback?.height ?? this.center.z) - clearances.min * 2,
    );
  }

  private random() {
    // Fast deterministic xorshift32 keeps resets reproducible.
    let value = this.state;
    value ^= value << 13;
    value ^= value >>> 17;
    value ^= value << 5;
    this.state = value >>> 0;
    return this.state / 0x1_0000_0000;
  }
}
