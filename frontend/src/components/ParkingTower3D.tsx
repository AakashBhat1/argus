"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { type ParkingSpace } from "@/lib/api";
import { Star, AlertTriangle, Info } from "lucide-react";
import {
  FLOOR_SPACING,
  type SpaceAnimation,
  getSpaceCoords,
  createCarMesh,
  createSlotBox,
  disposeObject,
  buildFloorPlanes,
} from "@/components/parking/threeHelpers";

interface ParkingTower3DProps {
  spaces: ParkingSpace[];
  onReleaseSpace: (spaceId: string, plateText: string) => void;
}

export default function ParkingTower3D({ spaces, onReleaseSpace }: ParkingTower3DProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoveredSpaceId, setHoveredSpaceId] = useState<string | null>(null);
  const [hoveredSpaceData, setHoveredSpaceData] = useState<ParkingSpace | null>(null);
  const [tooltipPosition, setTooltipPosition] = useState({ x: 0, y: 0 });

  // Refs to hold Three.js instances and maps
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const animFrameIdRef = useRef<number | null>(null);

  const spaceGroupsRef = useRef<Map<string, THREE.Group>>(new Map());
  const slotBoundingBoxesRef = useRef<Map<string, THREE.Mesh>>(new Map());
  const carMeshesRef = useRef<Map<string, THREE.Group>>(new Map());
  const activeAnimationsRef = useRef<SpaceAnimation[]>([]);
  const prevSpacesMapRef = useRef<Map<string, ParkingSpace>>(new Map());
  const spacesRef = useRef<ParkingSpace[]>(spaces);

  // Keep spaces state updated in a ref for animation loops and interaction handlers
  useEffect(() => {
    spacesRef.current = spaces;
  }, [spaces]);



  // Setup Three.js and mount
  useEffect(() => {
    if (typeof window === "undefined" || !containerRef.current) return;

    const container = containerRef.current;

    // Scene
    const scene = new THREE.Scene();
    sceneRef.current = scene;

    // Camera
    const camera = new THREE.PerspectiveCamera(
      40,
      container.clientWidth / container.clientHeight,
      1,
      100
    );
    camera.position.set(22, 18, 26);
    cameraRef.current = camera;

    // Renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    container.appendChild(renderer.domElement);
    rendererRef.current = renderer;

    // Controls
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.maxPolarAngle = Math.PI / 2 - 0.05; // Keep camera above ground
    controls.minDistance = 8;
    controls.maxDistance = 45;
    controls.target.set(0, FLOOR_SPACING, 0);
    controlsRef.current = controls;

    // Lighting
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.45);
    scene.add(ambientLight);

    const dirLight = new THREE.DirectionalLight(0xffffff, 0.75);
    dirLight.position.set(15, 30, 15);
    dirLight.castShadow = true;
    dirLight.shadow.mapSize.width = 1024;
    dirLight.shadow.mapSize.height = 1024;
    dirLight.shadow.camera.near = 0.5;
    dirLight.shadow.camera.far = 50;
    const d = 15;
    dirLight.shadow.camera.left = -d;
    dirLight.shadow.camera.right = d;
    dirLight.shadow.camera.top = d;
    dirLight.shadow.camera.bottom = -d;
    scene.add(dirLight);

    // Helper floors planes (G, 1, 2)
    buildFloorPlanes(scene);

    // Build spaces on startup
    spacesRef.current.forEach((sp) => {
      const coords = getSpaceCoords(sp);
      const group = new THREE.Group();
      group.position.set(coords.x, coords.y, coords.z);
      scene.add(group);

      spaceGroupsRef.current.set(sp.space_id, group);

      const profile = sp.profile_type || "normal";
      const boundingBox = createSlotBox(sp.is_occupied, profile);
      group.add(boundingBox);
      slotBoundingBoxesRef.current.set(sp.space_id, boundingBox);

      if (sp.is_occupied && sp.plate_text) {
        const car = createCarMesh(profile);
        group.add(car);
        carMeshesRef.current.set(sp.space_id, car);
      }

      prevSpacesMapRef.current.set(sp.space_id, { ...sp });
    });

    // Animation & render tick loop
    function animate() {
      const animationFrameId = requestAnimationFrame(animate);
      animFrameIdRef.current = animationFrameId;

      const activeAnimations = activeAnimationsRef.current;
      // Handle active smooth translation animations
      for (let i = activeAnimations.length - 1; i >= 0; i--) {
        const anim = activeAnimations[i];
        anim.progress += 0.05; // speed multiplier

        if (anim.progress >= 1.0) {
          anim.progress = 1.0;
          anim.mesh.position.set(0, anim.endY, anim.endZ);

          // Exit animators remove the visual mesh from screen after fading out
          if (anim.type === "exit") {
            const group = spaceGroupsRef.current.get(anim.spaceId);
            if (group) {
              group.remove(anim.mesh);
              disposeObject(anim.mesh);
            }
          }
          activeAnimations.splice(i, 1);
        } else {
          // Cubic ease-out interpolation
          const ease = 1 - Math.pow(1 - anim.progress, 3);

          const curY = anim.startY + (anim.endY - anim.startY) * ease;
          const curZ = anim.startZ + (anim.endZ - anim.startZ) * ease;
          anim.mesh.position.set(0, curY, curZ);

          // Adjust material opacity for fade transition
          const alpha = anim.opacityStart + (anim.opacityEnd - anim.opacityStart) * ease;
          anim.mesh.traverse((child) => {
            if (child instanceof THREE.Mesh && child.material) {
              const mats = Array.isArray(child.material) ? child.material : [child.material];
              mats.forEach((m) => {
                m.transparent = true;
                m.opacity = alpha;
              });
            }
          });
        }
      }

      // Pulse logic for blacklisted slots to draw attention
      const time = Date.now() * 0.003;
      slotBoundingBoxesRef.current.forEach((box, spaceId) => {
        const prev = prevSpacesMapRef.current.get(spaceId);
        if (prev && prev.is_occupied) {
          const profile = prev.profile_type || "normal";
          if (profile === "blacklist" && box.material) {
            const material = box.material as THREE.MeshBasicMaterial;
            material.opacity = 0.15 + Math.sin(time) * 0.1;
          }
        }
      });

      controls.update();
      renderer.render(scene, camera);
    }

    animate();

    // Resize handler
    function handleResize() {
      if (!camera || !renderer || !container) return;
      camera.aspect = container.clientWidth / container.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(container.clientWidth, container.clientHeight);
    }
    window.addEventListener("resize", handleResize);

    // Cleanup
    return () => {
      window.removeEventListener("resize", handleResize);
      if (animFrameIdRef.current !== null) {
        cancelAnimationFrame(animFrameIdRef.current);
      }
      controls.dispose();

      if (renderer.domElement && container.contains(renderer.domElement)) {
        container.removeChild(renderer.domElement);
      }
      renderer.dispose();

      // Clean up geometries and materials
      spaceGroupsRef.current.forEach((g) => {
        disposeObject(g);
      });
      spaceGroupsRef.current.clear();
      slotBoundingBoxesRef.current.clear();
      carMeshesRef.current.clear();
      activeAnimationsRef.current = [];
      prevSpacesMapRef.current.clear();
    };
  }, []);

  // Sync spaces on prop updates
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;

    spaces.forEach((sp) => {
      const prev = prevSpacesMapRef.current.get(sp.space_id);
      const group = spaceGroupsRef.current.get(sp.space_id);

      if (!group) return;

      const profile = sp.profile_type || "normal";

      // Check occupant status changes
      const wasOccupied = prev ? !!prev.is_occupied : false;
      const isOccupied = !!sp.is_occupied;

      if (wasOccupied !== isOccupied || (prev && prev.plate_text !== sp.plate_text)) {
        // Remove existing car mesh or active animation
        const existingCar = carMeshesRef.current.get(sp.space_id);
        if (existingCar) {
          group.remove(existingCar);
          disposeObject(existingCar);
          carMeshesRef.current.delete(sp.space_id);
        }

        // Remove active transitions for this slot
        activeAnimationsRef.current = activeAnimationsRef.current.filter(
          (a) => a.spaceId !== sp.space_id
        );

        // Update bounding box slot highlight colors
        const oldBox = slotBoundingBoxesRef.current.get(sp.space_id);
        if (oldBox) {
          group.remove(oldBox);
          disposeObject(oldBox);
        }
        const newBox = createSlotBox(isOccupied, profile);
        group.add(newBox);
        slotBoundingBoxesRef.current.set(sp.space_id, newBox);

        if (isOccupied && sp.plate_text) {
          // Entry animation: slide car down from Y=5.0
          const car = createCarMesh(profile);
          car.position.y = 5.0; // Start high
          group.add(car);
          carMeshesRef.current.set(sp.space_id, car);

          activeAnimationsRef.current.push({
            spaceId: sp.space_id,
            mesh: car,
            type: "entry",
            startY: 5.0,
            endY: 0.0,
            startZ: 0.0,
            endZ: 0.0,
            opacityStart: 0.0,
            opacityEnd: 1.0,
            progress: 0.0,
          });
        } else if (wasOccupied && !isOccupied && prev && prev.plate_text) {
          // Exit animation: slide car forward along Z-axis out of the slot
          const car = createCarMesh(prev.profile_type || "normal");
          group.add(car);

          activeAnimationsRef.current.push({
            spaceId: sp.space_id,
            mesh: car,
            type: "exit",
            startY: 0.0,
            endY: 0.0,
            startZ: 0.0,
            endZ: 4.0, // Slide out
            opacityStart: 1.0,
            opacityEnd: 0.0,
            progress: 0.0,
          });
        }
      }

      // Cache current state
      prevSpacesMapRef.current.set(sp.space_id, { ...sp });
    });
  }, [spaces]);

  // Click & hover raycasting helpers
  function hitHitbox(hit: THREE.Object3D): THREE.Object3D | null {
    const boxesSet = new Set<THREE.Object3D>(slotBoundingBoxesRef.current.values());
    let cur: THREE.Object3D | null = hit;
    while (cur) {
      if (boxesSet.has(cur)) return cur;
      cur = cur.parent;
    }
    return null;
  }

  function handleCanvasClick(event: React.MouseEvent<HTMLDivElement>) {
    if (!containerRef.current || !cameraRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();

    const mouseX = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    const mouseY = -((event.clientY - rect.top) / rect.height) * 2 + 1;

    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(new THREE.Vector2(mouseX, mouseY), cameraRef.current);

    const boundingMeshes: THREE.Mesh[] = [];
    slotBoundingBoxesRef.current.forEach((box) => {
      boundingMeshes.push(box);
    });
    const intersects = raycaster.intersectObjects(boundingMeshes);

    if (intersects.length > 0) {
      const resolvedBox = hitHitbox(intersects[0].object);
      if (resolvedBox) {
        let matchedId: string | null = null;
        slotBoundingBoxesRef.current.forEach((box, id) => {
          if (box === resolvedBox) {
            matchedId = id;
          }
        });

        if (matchedId) {
          const space = spacesRef.current.find((s) => s.space_id === matchedId);
          if (space && space.is_occupied && space.plate_text) {
            onReleaseSpace(space.space_id, space.plate_text);
          }
        }
      }
    }
  }

  function handleMouseMove(event: React.MouseEvent<HTMLDivElement>) {
    if (!containerRef.current || !cameraRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();

    const mouseX = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    const mouseY = -((event.clientY - rect.top) / rect.height) * 2 + 1;

    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(new THREE.Vector2(mouseX, mouseY), cameraRef.current);

    const boundingMeshes: THREE.Mesh[] = [];
    slotBoundingBoxesRef.current.forEach((box) => {
      boundingMeshes.push(box);
    });
    const intersects = raycaster.intersectObjects(boundingMeshes);

    if (intersects.length > 0) {
      const resolvedBox = hitHitbox(intersects[0].object);
      if (resolvedBox) {
        let matchedId: string | null = null;
        slotBoundingBoxesRef.current.forEach((box, id) => {
          if (box === resolvedBox) {
            matchedId = id;
          }
        });

        if (matchedId) {
          const space = spacesRef.current.find((s) => s.space_id === matchedId);
          if (space) {
            setHoveredSpaceId(matchedId);
            setHoveredSpaceData(space);
            setTooltipPosition({
              x: event.clientX - rect.left + 15,
              y: event.clientY - rect.top + 15,
            });
            containerRef.current.style.cursor = space.is_occupied ? "pointer" : "default";
            return;
          }
        }
      }
    }

    setHoveredSpaceId(null);
    setHoveredSpaceData(null);
    containerRef.current.style.cursor = "default";
  }

  return (
    <div
      ref={containerRef}
      className="relative w-full h-[520px] overflow-hidden rounded-2xl border border-slate-700/30 bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 shadow-2xl cursor-default select-none"
      onClick={handleCanvasClick}
      onMouseMove={handleMouseMove}
    >
      {/* Tooltip Overlay */}
      {hoveredSpaceId && hoveredSpaceData && (
        <div
          className={`absolute pointer-events-none z-50 bg-slate-950/95 border border-slate-700/50 rounded-xl p-3 shadow-2xl w-[200px] text-xs backdrop-blur-md flex flex-col gap-2 transition-all duration-100 ${
            hoveredSpaceData.profile_type === "vip"
              ? "border-emerald-500/50 shadow-emerald-500/5"
              : hoveredSpaceData.profile_type === "blacklist"
              ? "border-red-500/50 shadow-red-500/5 animate-pulse"
              : ""
          }`}
          style={{ left: tooltipPosition.x, top: tooltipPosition.y }}
        >
          <div className="flex flex-col border-b border-slate-800/80 pb-1.5">
            <span className="font-extrabold text-slate-100">Space {hoveredSpaceId}</span>
            <span className="text-[10px] text-slate-400">
              {hoveredSpaceData.floor === "G" ? "Ground" : `Floor ${hoveredSpaceData.floor}`} • Zone {hoveredSpaceData.zone}
            </span>
          </div>

          <div className="flex flex-col gap-1.5">
            {hoveredSpaceData.is_occupied ? (
              <>
                <div className="flex justify-between items-center">
                  <span className="text-slate-400">Plate:</span>
                  <span className="font-bold text-blue-400 font-mono tracking-wider">
                    {hoveredSpaceData.plate_text}
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-slate-400">Tier:</span>
                  <span
                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                      hoveredSpaceData.profile_type === "vip"
                        ? "bg-emerald-500/10 text-emerald-400"
                        : hoveredSpaceData.profile_type === "blacklist"
                        ? "bg-red-500/10 text-red-400"
                        : "bg-slate-800 text-slate-300"
                    }`}
                  >
                    {hoveredSpaceData.profile_type === "vip" ? (
                      <>
                        <Star className="w-2.5 h-2.5 fill-current" /> VIP
                      </>
                    ) : hoveredSpaceData.profile_type === "blacklist" ? (
                      <>
                        <AlertTriangle className="w-2.5 h-2.5" /> Warning
                      </>
                    ) : (
                      "Standard"
                    )}
                  </span>
                </div>
                <div className="text-[10px] text-blue-400/90 font-medium text-center mt-1.5 flex items-center justify-center gap-1 bg-blue-500/5 py-1 rounded">
                  <Info className="w-3 h-3" /> Click space to checkout
                </div>
              </>
            ) : (
              <div className="text-center font-extrabold text-slate-500 tracking-wider py-1 uppercase">
                Vacant
              </div>
            )}
          </div>
        </div>
      )}

      {/* Control instructions overlay */}
      <div className="absolute bottom-4 left-4 bg-slate-950/80 border border-slate-800/60 rounded-xl p-2.5 flex flex-col gap-1.5 pointer-events-none backdrop-blur-sm shadow-lg text-[10px] text-slate-400">
        <div className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
          Orbit: Left-Click + Drag
        </div>
        <div className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
          Pan: Right-Click + Drag
        </div>
        <div className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
          Zoom: Scroll
        </div>
      </div>
    </div>
  );
}
