import * as THREE from "three";
import { type ParkingSpace } from "@/lib/api";

// Grid dimensional constants
export const FLOOR_SPACING = 7.0;
export const ZONE_SPACING = 5.5;
export const SLOT_SPACING = 2.4;

export interface SpaceAnimation {
  spaceId: string;
  mesh: THREE.Group;
  type: "entry" | "exit";
  startY: number;
  endY: number;
  startZ: number;
  endZ: number;
  opacityStart: number;
  opacityEnd: number;
  progress: number;
}

// Helper: translate Space properties into 3D Coordinates
export function getSpaceCoords(space: ParkingSpace) {
  const floor = space.floor || "G";
  const zone = space.zone || "A";
  const match = space.space_id.match(/\d+$/);
  const number = match ? parseInt(match[0], 10) : 1;

  let y = 0.05;
  if (floor === "1") y = FLOOR_SPACING;
  if (floor === "2") y = FLOOR_SPACING * 2;

  let x = 0;
  if (zone === "A") x = -ZONE_SPACING;
  if (zone === "B") x = 0;
  if (zone === "C") x = ZONE_SPACING;

  // Position slots along Z axis
  const z = (number - 4.5) * SLOT_SPACING;

  return { x, y, z };
}

// Helper: create detailed car geometry representation
export function createCarMesh(profileType: string) {
  const group = new THREE.Group();

  let color = 0x0a84ff; // Normal
  if (profileType === "vip") color = 0x00ff66; // VIP
  if (profileType === "blacklist") color = 0xff3b30; // Blacklist

  // Body shell material
  const bodyMaterial = new THREE.MeshPhongMaterial({
    color,
    shininess: 90,
    specular: 0x333333,
  });

  const cabinMaterial = new THREE.MeshPhongMaterial({
    color: 0x111736,
    shininess: 100,
    transparent: true,
    opacity: 0.85,
  });

  const wheelMaterial = new THREE.MeshStandardMaterial({
    color: 0x0a0c16,
    roughness: 0.9,
  });

  // Chassis body
  const bodyGeo = new THREE.BoxGeometry(1.1, 0.35, 1.8);
  const body = new THREE.Mesh(bodyGeo, bodyMaterial);
  body.position.y = 0.22;
  body.castShadow = true;
  body.receiveShadow = true;
  group.add(body);

  // Cabin top
  const cabinGeo = new THREE.BoxGeometry(0.9, 0.32, 1.0);
  const cabin = new THREE.Mesh(cabinGeo, cabinMaterial);
  cabin.position.y = 0.555;
  cabin.position.z = -0.05;
  cabin.castShadow = true;
  group.add(cabin);

  // Wheel cylinders
  const wheelGeo = new THREE.CylinderGeometry(0.18, 0.18, 0.2, 12);
  wheelGeo.rotateZ(Math.PI / 2);

  const wheels = [
    { x: -0.6, y: 0.15, z: 0.5 },
    { x: 0.6, y: 0.15, z: 0.5 },
    { x: -0.6, y: 0.15, z: -0.5 },
    { x: 0.6, y: 0.15, z: -0.5 },
  ];

  wheels.forEach((pos) => {
    const w = new THREE.Mesh(wheelGeo, wheelMaterial);
    w.position.set(pos.x, pos.y, pos.z);
    w.castShadow = true;
    group.add(w);
  });

  return group;
}

// Helper: create space cell base box
export function createSlotBox(isOccupied: boolean, profileType: string) {
  const geometry = new THREE.BoxGeometry(1.5, 0.08, 2.1);

  let color = 0x1b234a; // Vacant
  let opacity = 0.12;

  if (isOccupied) {
    if (profileType === "vip") {
      color = 0x00ff66;
      opacity = 0.22;
    } else if (profileType === "blacklist") {
      color = 0xff3b30;
      opacity = 0.28;
    } else {
      color = 0x0a84ff;
      opacity = 0.18;
    }
  }

  const material = new THREE.MeshBasicMaterial({
    color,
    transparent: true,
    opacity,
    depthWrite: false,
  });

  const mesh = new THREE.Mesh(geometry, material);

  // Slot border line segments
  const edges = new THREE.EdgesGeometry(geometry);
  const lineMaterial = new THREE.LineBasicMaterial({
    color: isOccupied ? color : 0x536284,
    transparent: true,
    opacity: isOccupied ? 0.85 : 0.25,
  });
  const line = new THREE.LineSegments(edges, lineMaterial);
  mesh.add(line);

  return mesh;
}

// Helper to recursively dispose geometries and materials of an Object3D
export function disposeObject(obj: THREE.Object3D) {
  obj.traverse((child) => {
    if ((child as any).geometry) {
      (child as any).geometry.dispose();
    }
    if ((child as any).material) {
      const materials = Array.isArray((child as any).material)
        ? (child as any).material
        : [(child as any).material];
      materials.forEach((m: any) => m.dispose());
    }
  });
}

// Helper: build floor planes group
export function buildFloorPlanes(scene: THREE.Scene) {
  const floors = ["G", "1", "2"];
  const floorGroups: THREE.Group[] = [];

  floors.forEach((fName, idx) => {
    const yPos = idx * FLOOR_SPACING;
    const floorGroup = new THREE.Group();

    // Core flat rectangle plane
    const floorGeo = new THREE.PlaneGeometry(16, 22);
    floorGeo.rotateX(-Math.PI / 2);
    const floorMat = new THREE.MeshBasicMaterial({
      color: 0x07091b,
      transparent: true,
      opacity: 0.5,
      depthWrite: false,
    });
    const plane = new THREE.Mesh(floorGeo, floorMat);
    plane.position.y = yPos;
    floorGroup.add(plane);

    // Technical grid overlay
    const grid = new THREE.GridHelper(22, 11, 0x1f2b68, 0x11193d);
    grid.position.y = yPos + 0.005;
    if (Array.isArray(grid.material)) {
      grid.material.forEach((m) => {
        m.depthWrite = false;
        m.transparent = true;
        m.opacity = 0.6;
      });
    } else {
      grid.material.depthWrite = false;
      grid.material.transparent = true;
      grid.material.opacity = 0.6;
    }
    floorGroup.add(grid);

    // Visual layout tag at the front of each floor
    const labelCanvas = document.createElement("canvas");
    labelCanvas.width = 256;
    labelCanvas.height = 64;
    const ctx = labelCanvas.getContext("2d");
    if (ctx) {
      ctx.fillStyle = "rgba(11, 15, 36, 0.9)";
      ctx.fillRect(0, 0, 256, 64);
      ctx.strokeStyle = "#00ff66";
      ctx.lineWidth = 4;
      ctx.strokeRect(0, 0, 256, 64);
      ctx.fillStyle = "#f2f4f8";
      ctx.font = "bold 24px Inter, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(fName === "G" ? "GROUND FLOOR" : `FLOOR ${fName}`, 128, 32);
    }

    const labelTexture = new THREE.CanvasTexture(labelCanvas);
    const labelMat = new THREE.MeshBasicMaterial({
      map: labelTexture,
      transparent: true,
      side: THREE.DoubleSide,
    });
    const labelGeo = new THREE.PlaneGeometry(4, 1);
    const labelMesh = new THREE.Mesh(labelGeo, labelMat);
    labelMesh.position.set(0, yPos + 0.01, 11.2);
    labelMesh.rotateX(-Math.PI / 2);
    floorGroup.add(labelMesh);

    scene.add(floorGroup);
    floorGroups.push(floorGroup);
  });

  return floorGroups;
}
