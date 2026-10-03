import * as THREE from 'three';

export type SurfaceSample = {
  height: number;
  normalX: number;
  normalY: number;
  normalZ: number;
  relief: number;
};

/**
 * Source-agnostic velocity field consumed by the particle renderer.
 * A future CFDFlowField only needs to implement this contract.
 */
export abstract class FlowField {
  abstract readonly bounds: THREE.Box3;

  abstract sample(
    position: THREE.Vector3,
    time: number,
    target?: THREE.Vector3,
  ): THREE.Vector3;

  abstract surfaceAt(x: number, y: number): SurfaceSample | null;

  abstract getReferenceSpeed(): number;
}
