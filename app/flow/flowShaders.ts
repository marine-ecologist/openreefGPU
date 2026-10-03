export const trailVertexShader = /* glsl */ `
  attribute float flowSpeed;
  attribute float trailAlpha;
  varying float vSpeed;
  varying float vAlpha;

  void main() {
    vSpeed = flowSpeed;
    vAlpha = trailAlpha;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

export const trailFragmentShader = /* glsl */ `
  varying float vSpeed;
  varying float vAlpha;

  vec3 flowColour(float speed) {
    vec3 slow = vec3(0.05, 0.42, 0.95);
    vec3 medium = vec3(0.0, 0.95, 0.78);
    vec3 fast = vec3(1.0, 0.82, 0.22);
    return speed < 1.0
      ? mix(slow, medium, clamp(speed, 0.0, 1.0))
      : mix(medium, fast, clamp(speed - 1.0, 0.0, 1.0));
  }

  void main() {
    vec3 colour = flowColour(vSpeed);
    gl_FragColor = vec4(colour, vAlpha);
  }
`;
