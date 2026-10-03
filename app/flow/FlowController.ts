import * as THREE from 'three';

import { ParticleSystem } from './ParticleSystem';
import { ReefMeshFlowField, type ReefFlowOptions } from './ReefMeshFlowField';

export type FlowViewerOptions = ReefFlowOptions & {
  enabled: boolean;
  particleCount: number;
  trailLength: number;
};

export class FlowController {
  readonly field: ReefMeshFlowField;

  private particles: ParticleSystem;
  private enabled: boolean;
  private particleCount: number;
  private trailLength: number;
  private direction: number;

  constructor(
    private readonly scene: THREE.Scene,
    geometry: THREE.BufferGeometry,
    options: FlowViewerOptions,
  ) {
    this.enabled = options.enabled;
    this.particleCount = options.particleCount;
    this.trailLength = options.trailLength;
    this.direction = options.direction;
    this.field = new ReefMeshFlowField(geometry, options);
    this.particles = this.createParticles();
    this.scene.add(this.particles.object);
    this.particles.object.visible = this.enabled;
  }

  update(deltaSeconds: number, elapsedSeconds: number) {
    if (this.enabled) this.particles.update(deltaSeconds, elapsedSeconds);
  }

  setEnabled(enabled: boolean) {
    this.enabled = enabled;
    this.particles.object.visible = enabled;
  }

  setDirection(direction: number) {
    this.direction = direction;
    this.field.setDirection(direction);
    this.particles.setDirection(direction);
  }

  setSpeed(speed: number) {
    this.field.setSpeed(speed);
  }

  setWakeStrength(strength: number) {
    this.field.setWakeStrength(strength);
  }

  setSurfaceFollowing(strength: number) {
    this.field.setSurfaceFollowing(strength);
  }

  setTrailLength(length: number) {
    this.trailLength = length;
    this.particles.setTrailLength(length);
  }

  setParticleCount(count: number) {
    if (count === this.particleCount) return;
    const wasVisible = this.particles.object.visible;
    this.scene.remove(this.particles.object);
    this.particles.dispose();
    this.particleCount = count;
    this.particles = this.createParticles();
    this.particles.object.visible = wasVisible;
    this.scene.add(this.particles.object);
  }

  reset() {
    this.particles.reset();
  }

  dispose() {
    this.scene.remove(this.particles.object);
    this.particles.dispose();
  }

  private createParticles() {
    return new ParticleSystem(this.field, {
      count: this.particleCount,
      direction: this.direction,
      trailLength: this.trailLength,
    });
  }
}
