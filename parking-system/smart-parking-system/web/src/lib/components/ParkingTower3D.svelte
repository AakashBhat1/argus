<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import * as THREE from 'three';
    import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
    import type { Space } from '$lib/api';
    import { sseStore } from '$lib/api';

    // Props
    let { 
        spaces = [], 
        onReleaseSpace 
    }: { 
        spaces: Space[]; 
        onReleaseSpace: (spaceId: string, plateText: string) => void;
    } = $props();

    // DOM References
    let containerEl = $state<HTMLDivElement | null>(null);
    let hoveredSpaceId = $state<string | null>(null);
    let hoveredSpaceData = $state<Space | null>(null);
    let tooltipPosition = $state({ x: 0, y: 0 });

    // Three.js instances
    let scene: THREE.Scene;
    let camera: THREE.PerspectiveCamera;
    let renderer: THREE.WebGLRenderer;
    let controls: OrbitControls;
    let animationFrameId: number;

    // Mapping collections
    const spaceGroups: Map<string, THREE.Group> = new Map();
    const slotBoundingBoxes: Map<string, THREE.Mesh> = new Map();
    const carMeshes: Map<string, THREE.Group> = new Map();

    // Active animation list
    interface SpaceAnimation {
        spaceId: string;
        mesh: THREE.Group;
        type: 'entry' | 'exit';
        startY: number;
        endY: number;
        startZ: number;
        endZ: number;
        opacityStart: number;
        opacityEnd: number;
        progress: number;
    }
    let activeAnimations: SpaceAnimation[] = [];

    // Grid dimensional constants
    const FLOOR_SPACING = 7.0;
    const ZONE_SPACING = 5.5;
    const SLOT_SPACING = 2.4;

    // Previous spaces state cache to detect changes
    let prevSpacesMap: Map<string, Space> = new Map();

    // Helper: translate Space properties into 3D Coordinates
    function getSpaceCoords(space: Space) {
        const floor = space.floor || 'G';
        const zone = space.zone || 'A';
        const match = space.space_id.match(/\d+$/);
        const number = match ? parseInt(match[0], 10) : 1;

        let y = 0.05;
        if (floor === '1') y = FLOOR_SPACING;
        if (floor === '2') y = FLOOR_SPACING * 2;

        let x = 0;
        if (zone === 'A') x = -ZONE_SPACING;
        if (zone === 'B') x = 0;
        if (zone === 'C') x = ZONE_SPACING;

        // Position slots along Z axis
        const z = (number - 4.5) * SLOT_SPACING;

        return { x, y, z };
    }

    // Helper: profile checker
    function getProfileType(plateText: string | null): 'normal' | 'vip' | 'blacklist' | null {
        if (!plateText) return null;
        const profiles = $sseStore.profiles || [];
        const match = profiles.find(p => p.plate_text.toUpperCase() === plateText.toUpperCase());
        return match ? match.profile_type : 'normal';
    }

    // Helper: create detailed car geometry representation
    function createCarMesh(profileType: string) {
        const group = new THREE.Group();

        let color = 0x0a84ff; // Normal
        if (profileType === 'vip') color = 0x00ff66; // VIP
        if (profileType === 'blacklist') color = 0xff3b30; // Blacklist

        // Body shell material
        const bodyMaterial = new THREE.MeshPhongMaterial({ 
            color, 
            shininess: 90,
            specular: 0x333333
        });
        
        const cabinMaterial = new THREE.MeshPhongMaterial({ 
            color: 0x111736, 
            shininess: 100,
            transparent: true,
            opacity: 0.85
        });

        const wheelMaterial = new THREE.MeshStandardMaterial({ 
            color: 0x0a0c16,
            roughness: 0.9
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
            { x: 0.6, y: 0.15, z: -0.5 }
        ];

        wheels.forEach(pos => {
            const w = new THREE.Mesh(wheelGeo, wheelMaterial);
            w.position.set(pos.x, pos.y, pos.z);
            w.castShadow = true;
            group.add(w);
        });

        return group;
    }

    // Helper: create space cell base box
    function createSlotBox(isOccupied: boolean, profileType: string) {
        const geometry = new THREE.BoxGeometry(1.5, 0.08, 2.1);
        
        let color = 0x1b234a; // Vacant
        let opacity = 0.12;
        
        if (isOccupied) {
            if (profileType === 'vip') {
                color = 0x00ff66;
                opacity = 0.22;
            } else if (profileType === 'blacklist') {
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
            depthWrite: false
        });

        const mesh = new THREE.Mesh(geometry, material);
        
        // Slot border line segments
        const edges = new THREE.EdgesGeometry(geometry);
        const lineMaterial = new THREE.LineBasicMaterial({ 
            color: isOccupied ? color : 0x536284, 
            transparent: true, 
            opacity: isOccupied ? 0.85 : 0.25 
        });
        const line = new THREE.LineSegments(edges, lineMaterial);
        mesh.add(line);

        return mesh;
    }

    // Initialize scene elements
    function initThree() {
        if (!containerEl) return;

        // Scene
        scene = new THREE.Scene();
        scene.background = null; // Transparent background fits layout dark-mode

        // Camera
        camera = new THREE.PerspectiveCamera(40, containerEl.clientWidth / containerEl.clientHeight, 1, 100);
        camera.position.set(22, 18, 26);

        // Renderer
        renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        renderer.setSize(containerEl.clientWidth, containerEl.clientHeight);
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        renderer.shadowMap.enabled = true;
        renderer.shadowMap.type = THREE.PCFSoftShadowMap;
        containerEl.appendChild(renderer.domElement);

        // Controls
        controls = new OrbitControls(camera, renderer.domElement);
        controls.enableDamping = true;
        controls.dampingFactor = 0.05;
        controls.maxPolarAngle = Math.PI / 2 - 0.05; // Keep camera above ground
        controls.minDistance = 8;
        controls.maxDistance = 45;
        controls.target.set(0, FLOOR_SPACING, 0);

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
        const floors = ['G', '1', '2'];
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
                depthWrite: false
            });
            const plane = new THREE.Mesh(floorGeo, floorMat);
            plane.position.y = yPos;
            floorGroup.add(plane);

            // Technical grid overlay
            const grid = new THREE.GridHelper(22, 11, 0x1f2b68, 0x11193d);
            grid.position.y = yPos + 0.005;
            if (Array.isArray(grid.material)) {
                grid.material.forEach(m => {
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
            const labelCanvas = document.createElement('canvas');
            labelCanvas.width = 256;
            labelCanvas.height = 64;
            const ctx = labelCanvas.getContext('2d');
            if (ctx) {
                ctx.fillStyle = 'rgba(11, 15, 36, 0.9)';
                ctx.fillRect(0, 0, 256, 64);
                ctx.strokeStyle = '#00ff66';
                ctx.lineWidth = 4;
                ctx.strokeRect(0, 0, 256, 64);
                ctx.fillStyle = '#f2f4f8';
                ctx.font = 'bold 24px Inter, sans-serif';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(fName === 'G' ? 'GROUND FLOOR' : `FLOOR ${fName}`, 128, 32);
            }
            
            const labelTexture = new THREE.CanvasTexture(labelCanvas);
            const labelMat = new THREE.MeshBasicMaterial({ 
                map: labelTexture, 
                transparent: true,
                side: THREE.DoubleSide
            });
            const labelGeo = new THREE.PlaneGeometry(4, 1);
            const labelMesh = new THREE.Mesh(labelGeo, labelMat);
            labelMesh.position.set(0, yPos + 0.01, 11.2);
            labelMesh.rotateX(-Math.PI / 2);
            floorGroup.add(labelMesh);

            scene.add(floorGroup);
        });

        // Initialize space slot objects
        buildSpaces(spaces);

        // Start animation loops
        animate();
    }

    // Initial construction of slots mapping
    function buildSpaces(targetSpaces: Space[]) {
        targetSpaces.forEach(sp => {
            const coords = getSpaceCoords(sp);
            const group = new THREE.Group();
            group.position.set(coords.x, coords.y, coords.z);
            scene.add(group);

            spaceGroups.set(sp.space_id, group);

            // Create clickable slot boundaries
            const profile = getProfileType(sp.plate_text);
            const boundingBox = createSlotBox(!!sp.is_occupied, profile || 'normal');
            group.add(boundingBox);
            slotBoundingBoxes.set(sp.space_id, boundingBox);

            // Spawn car model if occupied initially
            if (sp.is_occupied && sp.plate_text) {
                const car = createCarMesh(profile || 'normal');
                group.add(car);
                carMeshes.set(sp.space_id, car);
            }

            prevSpacesMap.set(sp.space_id, { ...sp });
        });
    }

    // Helper to recursively dispose geometries and materials of an Object3D
    function disposeObject(obj: THREE.Object3D) {
        obj.traverse(child => {
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

    // Real-time synchronization triggered by store updates
    function syncSpaces(newSpaces: Space[]) {
        if (!scene) return;

        newSpaces.forEach(sp => {
            const prev = prevSpacesMap.get(sp.space_id);
            const group = spaceGroups.get(sp.space_id);

            if (!group) return;

            const profile = getProfileType(sp.plate_text);

            // Check occupant status changes
            const wasOccupied = prev ? !!prev.is_occupied : false;
            const isOccupied = !!sp.is_occupied;

            if (wasOccupied !== isOccupied || (prev && prev.plate_text !== sp.plate_text)) {
                // Remove existing car mesh or active animation
                const existingCar = carMeshes.get(sp.space_id);
                if (existingCar) {
                    group.remove(existingCar);
                    disposeObject(existingCar);
                    carMeshes.delete(sp.space_id);
                }

                // Remove active transitions for this slot
                activeAnimations = activeAnimations.filter(a => a.spaceId !== sp.space_id);

                // Update bounding box slot highlight colors
                const oldBox = slotBoundingBoxes.get(sp.space_id);
                if (oldBox) {
                    group.remove(oldBox);
                    disposeObject(oldBox);
                }
                const newBox = createSlotBox(isOccupied, profile || 'normal');
                group.add(newBox);
                slotBoundingBoxes.set(sp.space_id, newBox);

                if (isOccupied && sp.plate_text) {
                    // Entry animation: slide car down from above
                    const car = createCarMesh(profile || 'normal');
                    car.position.y = 5.0; // Start high
                    group.add(car);
                    carMeshes.set(sp.space_id, car);

                    activeAnimations.push({
                        spaceId: sp.space_id,
                        mesh: car,
                        type: 'entry',
                        startY: 5.0,
                        endY: 0.0,
                        startZ: 0.0,
                        endZ: 0.0,
                        opacityStart: 0.0,
                        opacityEnd: 1.0,
                        progress: 0.0
                    });
                } else if (wasOccupied && !isOccupied && prev && prev.plate_text) {
                    // Exit animation: slide car forward along Z-axis out of the slot
                    const car = createCarMesh(getProfileType(prev.plate_text) || 'normal');
                    group.add(car);

                    activeAnimations.push({
                        spaceId: sp.space_id,
                        mesh: car,
                        type: 'exit',
                        startY: 0.0,
                        endY: 0.0,
                        startZ: 0.0,
                        endZ: 4.0, // Slide out
                        opacityStart: 1.0,
                        opacityEnd: 0.0,
                        progress: 0.0
                    });
                }
            }

            // Cache current state
            prevSpacesMap.set(sp.space_id, { ...sp });
        });
    }

    // Animation & render tick loop
    function animate() {
        animationFrameId = requestAnimationFrame(animate);

        // Handle active smooth translation animations
        for (let i = activeAnimations.length - 1; i >= 0; i--) {
            const anim = activeAnimations[i];
            anim.progress += 0.05; // speed multiplier

            if (anim.progress >= 1.0) {
                anim.progress = 1.0;
                anim.mesh.position.set(0, anim.endY, anim.endZ);

                // Exit animators remove the visual mesh from screen after fading out
                if (anim.type === 'exit') {
                    const group = spaceGroups.get(anim.spaceId);
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
                anim.mesh.traverse(child => {
                    if (child instanceof THREE.Mesh && child.material) {
                        const mats = Array.isArray(child.material) ? child.material : [child.material];
                        mats.forEach(m => {
                            m.transparent = true;
                            m.opacity = alpha;
                        });
                    }
                });
            }
        }

        // Pulse logic for blacklisted slots to draw attention
        const time = Date.now() * 0.003;
        slotBoundingBoxes.forEach((box, spaceId) => {
            const prev = prevSpacesMap.get(spaceId);
            if (prev && prev.is_occupied) {
                const profile = getProfileType(prev.plate_text);
                if (profile === 'blacklist' && box.material) {
                    const material = box.material as THREE.MeshBasicMaterial;
                    material.opacity = 0.15 + Math.sin(time) * 0.1;
                }
            }
        });

        controls.update();
        renderer.render(scene, camera);
    }

    // Raycasting click handler to launch release space
    function handleCanvasClick(event: MouseEvent) {
        if (!containerEl) return;
        const rect = containerEl.getBoundingClientRect();
        
        // Normalized device coordinates
        const mouseX = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        const mouseY = -((event.clientY - rect.top) / rect.height) * 2 + 1;

        const raycaster = new THREE.Raycaster();
        raycaster.setFromCamera(new THREE.Vector2(mouseX, mouseY), camera);

        const boundingMeshes = Array.from(slotBoundingBoxes.values());
        const intersects = raycaster.intersectObjects(boundingMeshes);

        if (intersects.length > 0) {
            const resolvedBox = hitHitbox(intersects[0].object);
            if (resolvedBox) {
                let matchedId: string | null = null;
                
                for (const [id, box] of slotBoundingBoxes.entries()) {
                    if (box === resolvedBox) {
                        matchedId = id;
                        break;
                    }
                }

                if (matchedId) {
                    const space = spaces.find(s => s.space_id === matchedId);
                    if (space && space.is_occupied && space.plate_text) {
                        onReleaseSpace(space.space_id, space.plate_text);
                    }
                }
            }
        }
    }

    // Helper: traverse line segments and parent check to find the matching bounding box
    function hitHitbox(hit: THREE.Object3D): THREE.Object3D | null {
        const boxesSet = new Set<THREE.Object3D>(slotBoundingBoxes.values());
        let cur: THREE.Object3D | null = hit;
        while (cur) {
            if (boxesSet.has(cur)) return cur;
            cur = cur.parent;
        }
        return null;
    }

    // Mouse movement hover raycaster for tooltip and pointer cursor
    function handleMouseMove(event: MouseEvent) {
        if (!containerEl) return;
        const rect = containerEl.getBoundingClientRect();
        const mouseX = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        const mouseY = -((event.clientY - rect.top) / rect.height) * 2 + 1;

        const raycaster = new THREE.Raycaster();
        raycaster.setFromCamera(new THREE.Vector2(mouseX, mouseY), camera);

        const boundingMeshes = Array.from(slotBoundingBoxes.values());
        const intersects = raycaster.intersectObjects(boundingMeshes);

        if (intersects.length > 0) {
            const resolvedBox = hitHitbox(intersects[0].object);
            if (resolvedBox) {
                let matchedId: string | null = null;
                
                for (const [id, box] of slotBoundingBoxes.entries()) {
                    if (box === resolvedBox) {
                        matchedId = id;
                        break;
                    }
                }

                if (matchedId) {
                    const space = spaces.find(s => s.space_id === matchedId);
                    if (space) {
                        hoveredSpaceId = matchedId;
                        hoveredSpaceData = space;
                        tooltipPosition = {
                            x: event.clientX - rect.left + 15,
                            y: event.clientY - rect.top + 15
                        };
                        containerEl.style.cursor = space.is_occupied ? 'pointer' : 'default';
                        return;
                    }
                }
            }
        }

        hoveredSpaceId = null;
        hoveredSpaceData = null;
        containerEl.style.cursor = 'default';
    }

    // Handle viewport changes
    function handleResize() {
        if (!containerEl || !camera || !renderer) return;
        camera.aspect = containerEl.clientWidth / containerEl.clientHeight;
        camera.updateProjectionMatrix();
        renderer.setSize(containerEl.clientWidth, containerEl.clientHeight);
    }

    onMount(() => {
        if (typeof window === 'undefined') return;
        initThree();
        window.addEventListener('resize', handleResize);
    });

    onDestroy(() => {
        if (typeof window === 'undefined') return;
        
        window.removeEventListener('resize', handleResize);
        cancelAnimationFrame(animationFrameId);
        
        if (controls) controls.dispose();
        if (renderer) {
            if (renderer.domElement && containerEl && containerEl.contains(renderer.domElement)) {
                containerEl.removeChild(renderer.domElement);
            }
            renderer.dispose();
        }

        // Clean up geometries and materials
        spaceGroups.forEach(g => {
            disposeObject(g);
        });
    });

    // Reactive bindings for Svelte 5 state changes
    $effect(() => {
        syncSpaces(spaces);
    });
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div 
    bind:this={containerEl} 
    class="three-container"
    onclick={handleCanvasClick}
    onmousemove={handleMouseMove}
>
    <!-- Tooltip Overlay -->
    {#if hoveredSpaceId && hoveredSpaceData}
        {@const profile = getProfileType(hoveredSpaceData.plate_text)}
        <div 
            class="three-tooltip {profile || ''}" 
            style="left: {tooltipPosition.x}px; top: {tooltipPosition.y}px;"
        >
            <div class="tooltip-header">
                <span class="space-title">Space {hoveredSpaceId}</span>
                <span class="floor-zone">{hoveredSpaceData.floor === 'G' ? 'Ground' : `Floor ${hoveredSpaceData.floor}`} • Zone {hoveredSpaceData.zone}</span>
            </div>
            <div class="tooltip-body">
                {#if hoveredSpaceData.is_occupied}
                    <div class="info-row">
                        <span class="label">Plate:</span>
                        <span class="value plate mono">{hoveredSpaceData.plate_text}</span>
                    </div>
                    <div class="info-row">
                        <span class="label">Tier:</span>
                        <span class="value badge {profile || ''}">
                            {#if profile === 'vip'}
                                <i class="fas fa-star"></i> VIP
                            {:else if profile === 'blacklist'}
                                <i class="fas fa-triangle-exclamation"></i> Blacklist
                            {:else}
                                Standard
                            {/if}
                        </span>
                    </div>
                    <div class="click-instruction">
                        <i class="fas fa-hand-pointer"></i> Click space to release
                    </div>
                {:else}
                    <div class="status-vacant">VACANT</div>
                {/if}
            </div>
        </div>
    {/if}

    <!-- Instructions Layer -->
    <div class="instructions-panel">
        <div class="inst-item"><i class="fas fa-mouse-pointer"></i> Orbit: Left-Click + Drag</div>
        <div class="inst-item"><i class="fas fa-hand-rock"></i> Pan: Right-Click + Drag</div>
        <div class="inst-item"><i class="fas fa-expand-arrows-alt"></i> Zoom: Scroll</div>
    </div>
</div>

<style>
    .three-container {
        width: 100%;
        height: 100%;
        position: relative;
        overflow: hidden;
        border-radius: var(--radius);
        background: radial-gradient(circle at center, #090c1f 0%, #04050d 100%);
        border: 1px solid var(--border);
        min-height: 520px;
    }

    /* Tooltip styles */
    .three-tooltip {
        position: absolute;
        pointer-events: none;
        z-index: 100;
        background: rgba(11, 15, 36, 0.95);
        border: 1px solid var(--border);
        border-radius: var(--radius-sm);
        padding: 12px;
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
        width: 200px;
        color: var(--text-primary);
        font-family: var(--font-sans);
        font-size: 0.8rem;
        backdrop-filter: blur(6px);
        display: flex;
        flex-direction: column;
        gap: 8px;
        transition: opacity 0.1s ease;
    }

    .three-tooltip.vip {
        border-color: var(--accent);
        box-shadow: 0 0 10px rgba(0, 255, 102, 0.1);
    }

    .three-tooltip.blacklist {
        border-color: var(--danger);
        box-shadow: 0 0 10px rgba(255, 59, 48, 0.1);
    }

    .tooltip-header {
        display: flex;
        flex-direction: column;
        border-bottom: 1px solid var(--border);
        padding-bottom: 6px;
    }

    .space-title {
        font-weight: 800;
        font-size: 0.9rem;
        color: var(--text-primary);
    }

    .floor-zone {
        font-size: 0.7rem;
        color: var(--text-secondary);
    }

    .tooltip-body {
        display: flex;
        flex-direction: column;
        gap: 6px;
    }

    .info-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
    }

    .info-row .label {
        color: var(--text-secondary);
        font-weight: 500;
    }

    .info-row .value.plate {
        color: var(--accent);
        font-weight: 700;
    }

    .three-tooltip.blacklist .value.plate {
        color: var(--danger);
    }

    .info-row .value.badge {
        font-size: 0.65rem;
        font-weight: 700;
        padding: 1px 4px;
        border-radius: 3px;
        text-transform: uppercase;
    }

    .info-row .value.badge.vip {
        background: rgba(0, 255, 102, 0.12);
        color: var(--accent);
    }

    .info-row .value.badge.blacklist {
        background: rgba(255, 59, 48, 0.12);
        color: var(--danger);
    }

    .status-vacant {
        color: var(--text-muted);
        font-weight: 700;
        text-align: center;
        letter-spacing: 0.5px;
        font-size: 0.75rem;
        padding: 4px 0;
    }

    .click-instruction {
        margin-top: 4px;
        font-size: 0.68rem;
        color: var(--info);
        text-align: center;
        font-weight: 600;
    }

    /* Instructions Overlay panel */
    .instructions-panel {
        position: absolute;
        bottom: 12px;
        left: 12px;
        background: rgba(6, 8, 19, 0.75);
        border: 1px solid var(--border);
        border-radius: var(--radius-sm);
        padding: 8px 12px;
        display: flex;
        flex-direction: column;
        gap: 4px;
        pointer-events: none;
        backdrop-filter: blur(4px);
    }

    .inst-item {
        font-size: 0.68rem;
        color: var(--text-secondary);
        display: flex;
        align-items: center;
        gap: 6px;
        font-weight: 500;
    }

    .inst-item i {
        color: var(--accent);
        font-size: 0.72rem;
    }
</style>
