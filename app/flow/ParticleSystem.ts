import * as THREE from 'three';

import { FlowSeeds } from './FlowSeeds';
import { ParticleTrails } from './ParticleTrails';
import type { ReefMeshFlowField } from './ReefMeshFlowField';

export type ParticleOptions = {
  count: number;
  direction: number;
  trailLength: number;
};

const TRAIL_SEGMENTS = 10;

export class ParticleSystem {
  readonly object: THREE.LineSegments;

  private readonly positions: Float32Array;
  private readonly history: Float32Array;
  private readonly speedHistory: Float32Array;
  private readonly ages: Float32Array;
  private readonly lifetimes: Float32Array;
  private readonly vector = new THREE.Vector3();
  private readonly velocity = new THREE.Vector3();
  private readonly seeds: FlowSeeds;
  private readonly trails: ParticleTrails;
  private historyHead = 0;
  private snapshotElapsed = 0;
  private direction: number;
  private trailLength: number;

  constructor(
    private readonly field: ReefMeshFlowField,
    private readonly options: ParticleOptions,
  ) {
    this.direction = options.direction;
    this.trailLength = options.trailLength;
    this.positions = new Float32Array(options.count * 3);
    this.history = new Float32Array((TRAIL_SEGMENTS + 1) * options.count * 3);
    this.speedHistory = new Float32Array((TRAIL_SEGMENTS + 1) * options.count);
    this.ages = new Float32Array(options.count);
    this.lifetimes = new Float32Array(options.count);
    this.seeds = new FlowSeeds(field);
    this.trails = new ParticleTrails(options.count, TRAIL_SEGMENTS);
    this.object = this.trails.object;
    this.reset();
  }

  setDirection(direction: number) {
    this.direction = direction;
    this.reset();
  }

  setTrailLength(length: number) {
    this.trailLength = length;
    this.resetHistory();
  }

  reset() {
    this.seeds.resetRandom();
    for (let particle = 0; particle < this.options.count; particle += 1) {
      this.seeds.seed(this.vector, this.direction, 'domain');
      this.setPosition(particle, this.vector);
      this.ages[particle] = Math.random() * 4;
      this.lifetimes[particle] = 4 + Math.random() * 5;
    }
    this.resetHistory();
  }

  update(deltaSeconds: number, elapsedSeconds: number) {
    const delta = Math.min(deltaSeconds, 0.05);
    const referenceSpeed = Math.max(this.field.getReferenceSpeed(), 1e-6);
    const clearances = this.field.getClearanceRange();

    for (let particle = 0; particle < this.options.count; particle += 1) {
      const offset = particle * 3;
      this.vector.set(
        this.positions[offset],
        this.positions[offset + 1],
        this.positions[offset + 2],
      );
      this.field.sample(this.vector, elapsedSeconds, this.velocity);
      const newestSpeed = this.velocity.length() / referenceSpeed;
      this.vector.addScaledVector(this.velocity, delta);
      this.ages[particle] += delta;

      const surface = this.field.surfaceAt(this.vector.x, this.vector.y);
      const outside = !surface;
      const expired = this.ages[particle] > this.lifetimes[particle];
      if (outside || expired) {
        this.respawn(particle);
        continue;
      }

      // Collision guard: +Z points into the reef for this reconstruction.
      const minimumZ = surface.height - clearances.min * 0.38;
      if (this.vector.z > minimumZ) {
        this.vector.z = minimumZ;
        const normal = this.velocity.set(
          surface.normalX,
          surface.normalY,
          surface.normalZ,
        );
        this.vector.addScaledVector(normal, clearances.min * 0.16);
      }
      this.setPosition(particle, this.vector);
      this.speedHistory[this.historyHead * this.options.count + particle] =
        newestSpeed;
    }

    this.snapshotElapsed += delta;
    const snapshotInterval = Math.max(0.025, this.trailLength / TRAIL_SEGMENTS);
    if (this.snapshotElapsed >= snapshotInterval) {
      this.snapshotElapsed %= snapshotInterval;
      this.historyHead = (this.historyHead + 1) % (TRAIL_SEGMENTS + 1);
      const start = this.historyHead * this.positions.length;
      this.history.set(this.positions, start);
      const previous =
        (this.historyHead - 1 + TRAIL_SEGMENTS + 1) % (TRAIL_SEGMENTS + 1);
      const speedStart = this.historyHead * this.options.count;
      this.speedHistory.copyWithin(
        speedStart,
        previous * this.options.count,
        (previous + 1) * this.options.count,
      );
      this.trails.update(this.history, this.speedHistory, this.historyHead);
    }
  }

  dispose() {
    this.trails.dispose();
  }

  private respawn(particle: number) {
    this.seeds.seed(this.vector, this.direction, 'upstream');
    this.setPosition(particle, this.vector);
    this.ages[particle] = 0;
    this.lifetimes[particle] = 4 + Math.random() * 5;
    // Collapse this particle's whole trail to avoid a line across the domain.
    for (let slot = 0; slot < TRAIL_SEGMENTS + 1; slot += 1) {
      const offset = (slot * this.options.count + particle) * 3;
      this.history[offset] = this.vector.x;
      this.history[offset + 1] = this.vector.y;
      this.history[offset + 2] = this.vector.z;
      this.speedHistory[slot * this.options.count + particle] = 1;
    }
  }

  private resetHistory() {
    this.historyHead = 0;
    this.snapshotElapsed = 0;
    for (let slot = 0; slot < TRAIL_SEGMENTS + 1; slot += 1) {
      this.history.set(this.positions, slot * this.positions.length);
      this.speedHistory.fill(
        1,
        slot * this.options.count,
        (slot + 1) * this.options.count,
      );
    }
    this.trails.update(this.history, this.speedHistory, this.historyHead);
  }

  private setPosition(particle: number, position: THREE.Vector3) {
    const offset = particle * 3;
    this.positions[offset] = position.x;
    this.positions[offset + 1] = position.y;
    this.positions[offset + 2] = position.z;
  }
}
