'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowUp,
  Camera,
  Check,
  Expand,
  Focus,
  LoaderCircle,
  MousePointer2,
  RotateCcw,
  Waves,
} from 'lucide-react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { PLYLoader } from 'three/addons/loaders/PLYLoader.js';

import { Button } from '@/components/ui/button';

import { FlowController, type FlowViewerOptions } from './flow/FlowController';

type ViewId = 'textured' | 'sparse' | 'dense';
type DisplayMode =
  | 'solid'
  | 'wireframe'
  | 'solid-wire'
  | 'points'
  | 'points-mesh';

type ViewerApi = {
  fit: () => void;
  screenshot: () => void;
  fullscreen: () => void;
  setDisplayMode: (mode: DisplayMode) => void;
  setPointSize: (size: number) => void;
  setFlowEnabled: (enabled: boolean) => void;
  setFlowDirection: (direction: number) => void;
  setFlowSpeed: (speed: number) => void;
  setParticleCount: (count: number) => void;
  setTrailLength: (length: number) => void;
  setWakeStrength: (strength: number) => void;
  setSurfaceFollowing: (strength: number) => void;
  resetParticles: () => void;
};

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? '';
const asset = (name: string) => `${BASE_PATH}/models/patchreef/${name}`;

const assetBytes = {
  densePoints: 15_186_066,
  denseMesh: 5_612_636,
} as const;

const defaultFlowSettings: FlowViewerOptions = {
  enabled: true,
  direction: 270,
  speed: 0.58,
  particleCount: 12_000,
  trailLength: 1.25,
  wakeStrength: 0.72,
  surfaceFollowing: 0.82,
};

const views: Array<{
  id: ViewId;
  step: string;
  title: string;
  detail: string;
}> = [
  {
    id: 'textured',
    step: '01',
    title: 'Textured mesh',
    detail: 'Photoreal surface',
  },
  {
    id: 'sparse',
    step: '02',
    title: 'Sparse cloud',
    detail: 'Original point colour',
  },
  { id: 'dense', step: '03', title: 'Dense + mesh', detail: '1 px points' },
];

type CloudModel = { url: string; label: string; source: string };

export function ReefViewer({ cloudModel }: { cloudModel?: CloudModel }) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const apiRef = useRef<ViewerApi | null>(null);
  const displayModeRef = useRef<DisplayMode>('solid');
  const pointSizeRef = useRef(1);
  const [view, setView] = useState<ViewId>('textured');
  const [displayMode, setDisplayMode] = useState<DisplayMode>('solid');
  const [pointSize, setPointSize] = useState(1);
  const [loading, setLoading] = useState(true);
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState('Loading compact textured mesh…');
  const [error, setError] = useState<string | null>(null);
  const [flowEnabled, setFlowEnabled] = useState(defaultFlowSettings.enabled);
  const [flowDirection, setFlowDirection] = useState(
    defaultFlowSettings.direction,
  );
  const [flowSpeed, setFlowSpeed] = useState(defaultFlowSettings.speed);
  const [particleCount, setParticleCount] = useState(
    defaultFlowSettings.particleCount,
  );
  const [trailLength, setTrailLength] = useState(
    defaultFlowSettings.trailLength,
  );
  const [wakeStrength, setWakeStrength] = useState(
    defaultFlowSettings.wakeStrength,
  );
  const [surfaceFollowing, setSurfaceFollowing] = useState(
    defaultFlowSettings.surfaceFollowing,
  );
  const [flowStatus, setFlowStatus] = useState(
    'Preparing medium mesh terrain…',
  );
  const flowSettingsRef = useRef({ ...defaultFlowSettings });

  const loadView = useCallback((nextView: ViewId) => {
    setLoading(true);
    setProgress(0);
    setError(null);
    setStatus(
      nextView === 'textured'
        ? 'Loading compact textured mesh…'
        : nextView === 'sparse'
          ? 'Loading colour sparse cloud…'
          : 'Loading compact dense reconstruction…',
    );
    const nextMode =
      nextView === 'dense'
        ? 'points-mesh'
        : nextView === 'sparse'
          ? 'points'
          : 'solid';
    displayModeRef.current = nextMode;
    setDisplayMode(nextMode);
    setView(nextView);
  }, []);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x222222);
    scene.fog = new THREE.FogExp2(0x222222, 0.00012);

    const camera = new THREE.PerspectiveCamera(42, 1, 0.01, 1_000_000);
    camera.up.set(0, 0, -1);
    const renderer = new THREE.WebGLRenderer({
      antialias: true,
      preserveDrawingBuffer: true,
      powerPreference: 'high-performance',
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    viewport.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.screenSpacePanning = true;
    controls.mouseButtons.LEFT = THREE.MOUSE.ROTATE;
    controls.mouseButtons.MIDDLE = THREE.MOUSE.DOLLY;
    controls.mouseButtons.RIGHT = THREE.MOUSE.PAN;
    controls.touches.ONE = THREE.TOUCH.ROTATE;
    controls.touches.TWO = THREE.TOUCH.DOLLY_PAN;

    scene.add(new THREE.HemisphereLight(0xffffff, 0x303030, 2.2));
    const sun = new THREE.DirectionalLight(0xffffff, 2.5);
    sun.position.set(4, -3, -8);
    scene.add(sun);

    const root = new THREE.Group();
    scene.add(root);
    let disposed = false;
    let points: THREE.Points[] = [];
    let meshes: THREE.Mesh[] = [];
    let generatedPoints: THREE.Points[] = [];
    let edges: THREE.LineSegments[] = [];
    let currentMode: DisplayMode = displayModeRef.current;
    let currentPointSize = pointSizeRef.current;
    let flowController: FlowController | null = null;
    let flowSurfaceLabel = 'medium terrain';
    const timer = new THREE.Timer();
    timer.connect(document);

    const resize = () => {
      const width = viewport.clientWidth;
      const height = viewport.clientHeight;
      renderer.setSize(width, height, false);
      camera.aspect = width / Math.max(height, 1);
      camera.updateProjectionMatrix();
    };

    const fit = () => {
      const box = new THREE.Box3().setFromObject(root);
      if (box.isEmpty()) return;
      const center = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3());
      const radius = Math.max(size.length() * 0.5, 0.001);
      const distance =
        radius / Math.sin(THREE.MathUtils.degToRad(camera.fov * 0.5));
      // Start above the reef, looking squarely across the patch like the field reference.
      const direction = new THREE.Vector3(0.08, -1.5, -0.82).normalize();
      camera.position.copy(center).add(direction.multiplyScalar(distance));
      camera.near = Math.max(radius / 10_000, 0.0001);
      camera.far = Math.max(radius * 10_000, 1_000);
      camera.updateProjectionMatrix();
      controls.target.copy(center);
      controls.update();
      scene.fog = new THREE.FogExp2(0x222222, 0.16 / Math.max(radius, 1));
    };

    const clearEdges = () => {
      edges.forEach((edge) => {
        edge.parent?.remove(edge);
        edge.geometry.dispose();
        (edge.material as THREE.Material).dispose();
      });
      edges = [];
    };

    const applyDisplayMode = (mode: DisplayMode) => {
      currentMode = mode;
      clearEdges();
      meshes.forEach((mesh) => {
        mesh.visible = mode !== 'points';
        const materials = Array.isArray(mesh.material)
          ? mesh.material
          : [mesh.material];
        materials.forEach((material) => {
          if ('wireframe' in material) {
            (material as THREE.MeshStandardMaterial).wireframe =
              mode === 'wireframe';
          }
        });
        if (mode === 'solid-wire') {
          const edge = new THREE.LineSegments(
            new THREE.EdgesGeometry(mesh.geometry, 22),
            new THREE.LineBasicMaterial({
              color: 0x222222,
              transparent: true,
              opacity: 0.62,
            }),
          );
          mesh.add(edge);
          edges.push(edge);
        }
      });
      points.forEach((object) => {
        object.visible = mode === 'points' || mode === 'points-mesh';
      });
    };

    const setSize = (size: number) => {
      currentPointSize = size;
      points.forEach((object) => {
        const materials = Array.isArray(object.material)
          ? object.material
          : [object.material];
        materials.forEach((material) => {
          (material as THREE.PointsMaterial).size = size;
          (material as THREE.PointsMaterial).sizeAttenuation = false;
        });
      });
    };

    const collectObjects = () => {
      root.traverse((object) => {
        if ((object as THREE.Points).isPoints)
          points.push(object as THREE.Points);
        if ((object as THREE.Mesh).isMesh) meshes.push(object as THREE.Mesh);
      });

      if (view === 'textured') {
        meshes.forEach((mesh) => {
          const pointObject = new THREE.Points(
            mesh.geometry,
            new THREE.PointsMaterial({
              color: 0x3498db,
              size: currentPointSize,
              sizeAttenuation: false,
            }),
          );
          pointObject.position.copy(mesh.position);
          pointObject.quaternion.copy(mesh.quaternion);
          pointObject.scale.copy(mesh.scale);
          mesh.parent?.add(pointObject);
          generatedPoints.push(pointObject);
        });
        points.push(...generatedPoints);
      }
      setSize(currentPointSize);
      applyDisplayMode(currentMode);
    };

    const disposeMaterial = (material: THREE.Material) => {
      Object.values(material).forEach((value) => {
        if (value instanceof THREE.Texture) value.dispose();
      });
      material.dispose();
    };

    const clearRoot = () => {
      clearEdges();
      root.traverse((object) => {
        const renderable = object as THREE.Mesh | THREE.Points;
        renderable.geometry?.dispose();
        if (renderable.material) {
          const materials = Array.isArray(renderable.material)
            ? renderable.material
            : [renderable.material];
          materials.forEach(disposeMaterial);
        }
      });
      root.clear();
      points = [];
      meshes = [];
      generatedPoints = [];
    };

    const countVertices = () => {
      let count = 0;
      root.traverse((object) => {
        if (!generatedPoints.includes(object as THREE.Points)) {
          count +=
            (object as THREE.Mesh).geometry?.attributes?.position?.count ?? 0;
        }
      });
      return count;
    };

    const ready = () => {
      if (disposed) return;
      collectObjects();
      fit();
      const count = new Intl.NumberFormat().format(countVertices());
      setStatus(`${count} vertices · compact web assets`);
      setProgress(100);
      setLoading(false);
    };

    const fail = (reason: unknown) => {
      console.error(reason);
      if (disposed) return;
      setLoading(false);
      setError('This compact model could not be loaded.');
      setStatus('Load failed');
    };

    const onProgress = (event: ProgressEvent<EventTarget>) => {
      if (disposed || !event.total) return;
      setProgress(Math.min(99, Math.round((event.loaded / event.total) * 100)));
    };

    const addPlyPoints = (geometry: THREE.BufferGeometry) => {
      geometry.computeBoundingSphere();
      const hasColors = geometry.hasAttribute('color');
      root.add(
        new THREE.Points(
          geometry,
          new THREE.PointsMaterial({
            color: hasColors ? 0xffffff : 0x3498db,
            vertexColors: hasColors,
            size: 1,
            sizeAttenuation: false,
          }),
        ),
      );
    };

    const addPlyMesh = (geometry: THREE.BufferGeometry) => {
      if (!geometry.hasAttribute('normal')) geometry.computeVertexNormals();
      root.add(
        new THREE.Mesh(
          geometry,
          new THREE.MeshStandardMaterial({
            color: 0x375a7f,
            roughness: 0.9,
            metalness: 0,
            side: THREE.DoubleSide,
            transparent: view === 'dense',
            opacity: view === 'dense' ? 0.62 : 1,
            polygonOffset: view === 'dense',
            polygonOffsetFactor: 1,
            polygonOffsetUnits: 1,
          }),
        ),
      );
    };

    clearRoot();

    // Prefer the pipeline's medium Patch reef mesh. The compact web example is
    // a checked-in fallback, so this checkout remains immediately runnable.
    const prepareFlowSurface = (geometry: THREE.BufferGeometry) => {
      if (disposed) {
        geometry.dispose();
        return;
      }
      setFlowStatus('Building BVH terrain cache…');
      window.setTimeout(() => {
        if (disposed) {
          geometry.dispose();
          return;
        }
        try {
          flowController = new FlowController(
            scene,
            geometry,
            flowSettingsRef.current,
          );
          setFlowStatus(
            `${Math.round(flowSettingsRef.current.particleCount / 1000)}k particles · ${flowSurfaceLabel}`,
          );
        } catch (reason) {
          console.error(reason);
          setFlowStatus('Flow terrain unavailable');
        } finally {
          geometry.dispose();
        }
      }, 0);
    };
    const loadFlowSurface = (name: string, fallback = false) => {
      new PLYLoader().load(
        asset(name),
        prepareFlowSurface,
        undefined,
        (reason) => {
          if (!fallback) {
            flowSurfaceLabel = 'interactive terrain';
            setFlowStatus('Medium mesh absent · using interactive mesh…');
            loadFlowSurface('scene_mesh_compact.ply', true);
            return;
          }
          console.error(reason);
          if (!disposed) setFlowStatus('Flow terrain unavailable');
        },
      );
    };
    if (!cloudModel) loadFlowSurface('scene_mesh_medium.ply');

    if (cloudModel) {
      new GLTFLoader().load(
        cloudModel.url,
        (gltf) => {
          root.add(gltf.scene);
          ready();
        },
        onProgress,
        fail,
      );
    } else if (view === 'textured') {
      new GLTFLoader().load(
        asset('scene_mesh_compact_textured.glb'),
        (gltf) => {
          root.add(gltf.scene);
          ready();
        },
        onProgress,
        fail,
      );
    } else if (view === 'sparse') {
      new PLYLoader().load(
        asset('points3D_compact.ply'),
        (geometry) => {
          addPlyPoints(geometry);
          ready();
        },
        onProgress,
        fail,
      );
    } else {
      let completed = 0;
      const denseTransfer: Array<{ loaded: number; total: number }> = [
        { loaded: 0, total: assetBytes.densePoints },
        { loaded: 0, total: assetBytes.denseMesh },
      ];
      const finishPart = () => {
        completed += 1;
        if (completed === 2) ready();
      };
      const partProgress =
        (index: number) => (event: ProgressEvent<EventTarget>) => {
          denseTransfer[index].loaded = event.loaded;
          if (event.total) denseTransfer[index].total = event.total;
          const loaded = denseTransfer.reduce(
            (sum, part) => sum + part.loaded,
            0,
          );
          const total = denseTransfer.reduce(
            (sum, part) => sum + part.total,
            0,
          );
          setProgress(Math.min(99, Math.round((loaded / total) * 100)));
        };
      const loader = new PLYLoader();
      loader.load(
        asset('scene_dense_compact.ply'),
        (geometry) => {
          addPlyPoints(geometry);
          finishPart();
        },
        partProgress(0),
        fail,
      );
      loader.load(
        asset('scene_mesh_compact.ply'),
        (geometry) => {
          addPlyMesh(geometry);
          finishPart();
        },
        partProgress(1),
        fail,
      );
    }

    const screenshot = () => {
      renderer.render(scene, camera);
      const link = document.createElement('a');
      link.download = `openreef-${cloudModel ? 'cloud-model' : `patchreef-${view}`}.png`;
      link.href = renderer.domElement.toDataURL('image/png');
      link.click();
    };

    apiRef.current = {
      fit,
      screenshot,
      fullscreen: () => viewport.closest('.workspace')?.requestFullscreen(),
      setDisplayMode: applyDisplayMode,
      setPointSize: setSize,
      setFlowEnabled: (enabled) => flowController?.setEnabled(enabled),
      setFlowDirection: (direction) => flowController?.setDirection(direction),
      setFlowSpeed: (speed) => flowController?.setSpeed(speed),
      setParticleCount: (count) => {
        flowController?.setParticleCount(count);
        setFlowStatus(
          `${Math.round(count / 1000)}k particles · ${flowSurfaceLabel}`,
        );
      },
      setTrailLength: (length) => flowController?.setTrailLength(length),
      setWakeStrength: (strength) => flowController?.setWakeStrength(strength),
      setSurfaceFollowing: (strength) =>
        flowController?.setSurfaceFollowing(strength),
      resetParticles: () => flowController?.reset(),
    };

    resize();
    window.addEventListener('resize', resize);
    renderer.setAnimationLoop((timestamp) => {
      timer.update(timestamp);
      const delta = timer.getDelta();
      flowController?.update(delta, timer.getElapsed());
      controls.update();
      renderer.render(scene, camera);
    });

    return () => {
      disposed = true;
      window.removeEventListener('resize', resize);
      renderer.setAnimationLoop(null);
      timer.dispose();
      controls.dispose();
      flowController?.dispose();
      clearRoot();
      renderer.dispose();
      renderer.domElement.remove();
      apiRef.current = null;
    };
  }, [view, cloudModel]);

  const changeDisplayMode = (mode: DisplayMode) => {
    displayModeRef.current = mode;
    setDisplayMode(mode);
    apiRef.current?.setDisplayMode(mode);
  };

  const changePointSize = (size: number) => {
    pointSizeRef.current = size;
    setPointSize(size);
    apiRef.current?.setPointSize(size);
  };

  const showPointSize =
    displayMode === 'points' || displayMode === 'points-mesh';

  const changeFlowEnabled = (enabled: boolean) => {
    flowSettingsRef.current.enabled = enabled;
    setFlowEnabled(enabled);
    apiRef.current?.setFlowEnabled(enabled);
  };

  const changeFlowDirection = (direction: number) => {
    flowSettingsRef.current.direction = direction;
    setFlowDirection(direction);
    apiRef.current?.setFlowDirection(direction);
  };

  const changeFlowSpeed = (speed: number) => {
    flowSettingsRef.current.speed = speed;
    setFlowSpeed(speed);
    apiRef.current?.setFlowSpeed(speed);
  };

  const changeParticleCount = (count: number) => {
    flowSettingsRef.current.particleCount = count;
    setParticleCount(count);
    apiRef.current?.setParticleCount(count);
  };

  const changeTrailLength = (length: number) => {
    flowSettingsRef.current.trailLength = length;
    setTrailLength(length);
    apiRef.current?.setTrailLength(length);
  };

  const changeWakeStrength = (strength: number) => {
    flowSettingsRef.current.wakeStrength = strength;
    setWakeStrength(strength);
    apiRef.current?.setWakeStrength(strength);
  };

  const changeSurfaceFollowing = (strength: number) => {
    flowSettingsRef.current.surfaceFollowing = strength;
    setSurfaceFollowing(strength);
    apiRef.current?.setSurfaceFollowing(strength);
  };

  return (
    <>
      <div ref={viewportRef} className="reef-canvas" />
      <div className="viewer-wash" aria-hidden="true" />

      <section className="model-heading" aria-labelledby="model-title">
        <p className="eyebrow">
          <span className="live-dot" />{' '}
          {cloudModel ? 'Cloud result' : 'Live example'}
        </p>
        <h1 id="model-title">{cloudModel?.label ?? 'Patch reef'}</h1>
        <p className="model-source">
          {cloudModel?.source ?? 'DJI6Run4GPSCL · compact reconstruction'}
        </p>
      </section>

      <div className="viewer-status" aria-live="polite">
        {loading ? (
          <LoaderCircle className="spin" aria-hidden="true" />
        ) : (
          <Check aria-hidden="true" />
        )}
        <span>{error ?? status}</span>
        {loading && <strong>{progress}% transferred</strong>}
      </div>

      {!cloudModel && (
        <nav className="view-switcher" aria-label="Reconstruction stage">
          {views.map((item) => (
            <button
              type="button"
              key={item.id}
              className="view-option"
              data-active={view === item.id}
              onClick={() => item.id !== view && loadView(item.id)}
            >
              <span className="view-step">{item.step}</span>
              <span>
                <strong>{item.title}</strong>
                <small>{item.detail}</small>
              </span>
            </button>
          ))}
        </nav>
      )}

      <aside className="controls-panel" aria-label="Viewer controls">
        <div className="controls-title">
          <span>Viewer controls</span>
          <MousePointer2 aria-hidden="true" />
        </div>

        <Button
          variant="outline"
          onClick={() => apiRef.current?.fit()}
          title="Return to the starting angle"
        >
          <Focus data-icon="inline-start" /> Reset view
        </Button>

        <label className="control-label" htmlFor="display-mode">
          Display
        </label>
        <select
          id="display-mode"
          value={displayMode}
          onChange={(event) =>
            changeDisplayMode(event.target.value as DisplayMode)
          }
        >
          {view === 'dense' && (
            <option value="points-mesh">Points + mesh</option>
          )}
          {view !== 'sparse' && <option value="solid">Textured / solid</option>}
          {view !== 'sparse' && <option value="wireframe">Wire mesh</option>}
          {view !== 'sparse' && (
            <option value="solid-wire">Solid + wireframe</option>
          )}
          <option value="points">Points</option>
        </select>

        {showPointSize && (
          <label className="range-control" htmlFor="point-size">
            <span>Point size</span>
            <output>{pointSize} px</output>
            <input
              id="point-size"
              type="range"
              min="1"
              max="12"
              step="1"
              value={pointSize}
              onChange={(event) => changePointSize(Number(event.target.value))}
            />
          </label>
        )}

        <div className="control-actions">
          <Button
            variant="outline"
            size="icon"
            onClick={() => apiRef.current?.screenshot()}
            aria-label="Save screenshot"
          >
            <Camera />
          </Button>
          <Button
            variant="outline"
            size="icon"
            onClick={() => apiRef.current?.fullscreen()}
            aria-label="Enter full screen"
          >
            <Expand />
          </Button>
        </div>
      </aside>

      {!cloudModel && (
        <aside className="flow-panel" aria-label="Flow controls">
          <div className="controls-title">
            <span>Mesh flow</span>
            <Waves aria-hidden="true" />
          </div>

          <button
            type="button"
            className="flow-toggle"
            data-active={flowEnabled}
            aria-pressed={flowEnabled}
            onClick={() => changeFlowEnabled(!flowEnabled)}
          >
            <span>
              <span className="flow-toggle-dot" /> Flow
            </span>
            <strong>{flowEnabled ? 'On' : 'Off'}</strong>
          </button>
          <p className="flow-status">{flowStatus}</p>

          <label className="range-control" htmlFor="flow-direction">
            <span>Direction</span>
            <output>{flowDirection}°</output>
            <input
              id="flow-direction"
              type="range"
              min="0"
              max="360"
              step="5"
              value={flowDirection}
              onChange={(event) =>
                changeFlowDirection(Number(event.target.value))
              }
            />
          </label>

          <label className="range-control" htmlFor="flow-speed">
            <span>Relative speed</span>
            <output>{flowSpeed.toFixed(2)}</output>
            <input
              id="flow-speed"
              type="range"
              min="0"
              max="1"
              step="0.02"
              value={flowSpeed}
              onChange={(event) => changeFlowSpeed(Number(event.target.value))}
            />
          </label>

          <label className="control-label" htmlFor="particle-density">
            Particle density
          </label>
          <select
            id="particle-density"
            value={particleCount}
            onChange={(event) =>
              changeParticleCount(Number(event.target.value))
            }
          >
            <option value="5000">Low · 5k</option>
            <option value="12000">Medium · 12k</option>
            <option value="20000">High · 20k</option>
          </select>

          <label className="range-control" htmlFor="trail-length">
            <span>Trail length</span>
            <output>{trailLength.toFixed(1)} s</output>
            <input
              id="trail-length"
              type="range"
              min="0.5"
              max="2.5"
              step="0.1"
              value={trailLength}
              onChange={(event) =>
                changeTrailLength(Number(event.target.value))
              }
            />
          </label>

          <label className="range-control" htmlFor="wake-strength">
            <span>Wake strength</span>
            <output>{wakeStrength.toFixed(2)}</output>
            <input
              id="wake-strength"
              type="range"
              min="0"
              max="1"
              step="0.02"
              value={wakeStrength}
              onChange={(event) =>
                changeWakeStrength(Number(event.target.value))
              }
            />
          </label>

          <label className="range-control" htmlFor="surface-following">
            <span>Surface following</span>
            <output>{surfaceFollowing.toFixed(2)}</output>
            <input
              id="surface-following"
              type="range"
              min="0"
              max="1"
              step="0.02"
              value={surfaceFollowing}
              onChange={(event) =>
                changeSurfaceFollowing(Number(event.target.value))
              }
            />
          </label>

          <Button
            variant="outline"
            onClick={() => apiRef.current?.resetParticles()}
            title="Reseed flow particles"
          >
            <RotateCcw data-icon="inline-start" /> Reset particles
          </Button>
        </aside>
      )}

      {!cloudModel && (
        <div
          className="current-indicator"
          data-active={flowEnabled}
          aria-label={`Current direction ${flowDirection} degrees`}
        >
          <span>Current</span>
          <ArrowUp
            style={{ transform: `rotate(${flowDirection}deg)` }}
            aria-hidden="true"
          />
          <strong>{flowDirection}°</strong>
        </div>
      )}

      {loading && (
        <progress
          className="progress-track"
          aria-label="Model transfer"
          max={100}
          value={progress}
        >
          {progress}%
        </progress>
      )}

      <div
        className="interaction-guide"
        aria-label="How to move around the model"
      >
        <span>
          <strong>Rotate</strong> Left-drag
        </span>
        <span>
          <strong>Pan</strong> Right-drag
        </span>
        <span>
          <strong>Zoom</strong> Wheel or pinch
        </span>
        <span className="touch-guide">
          <strong>Touch</strong> One finger rotates · two fingers move and zoom
        </span>
      </div>
    </>
  );
}
