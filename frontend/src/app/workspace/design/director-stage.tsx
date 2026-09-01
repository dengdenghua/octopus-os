"use client";

import { useEffect, useRef, useState } from "react";
import {
  BoxIcon,
  CameraIcon,
  ChevronLeftIcon,
  CirclePlayIcon,
  DownloadIcon,
  FootprintsIcon,
  Globe2Icon,
  KeyboardIcon,
  Move3DIcon,
  PanelRightCloseIcon,
  PauseIcon,
  PlusIcon,
  RectangleHorizontalIcon,
  Redo2Icon,
  SmartphoneIcon,
  Undo2Icon,
  UserRoundIcon,
  VideoIcon,
  XIcon,
} from "lucide-react";
import * as THREE from "three";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBackendBaseURL } from "@/core/config";
import { cn } from "@/lib/utils";

type Pose =
  | "stand"
  | "tpose"
  | "walk"
  | "run"
  | "jump"
  | "sit"
  | "squat"
  | "kneel"
  | "lie"
  | "drive"
  | "wave"
  | "hands_up"
  | "bow"
  | "akimbo"
  | "think"
  | "fight"
  | "aim"
  | "sword"
  | "spell";
type BodyType = "mannequin" | "female" | "child";
type ModelPart = {
  id: string;
  name: string;
  shape: "box" | "sphere" | "cylinder" | "cone";
  size: number[];
  position: number[];
  rotation: number[];
  color: string;
  metalness?: number;
  roughness?: number;
};
type ModelEntity = {
  id: string;
  type: "model";
  name: string;
  position?: number[];
  rotation?: number[];
  scale?: number[];
  parts: ModelPart[];
};
type PropEntity = {
  id: string;
  type: "prop";
  name: string;
  assetId: string;
  shape: "box" | "sphere" | "cylinder" | "cone";
  size: number[];
  color: string;
  position?: number[];
  rotation?: number[];
  scale?: number[];
};
type TimelineTrack = {
  id: string;
  type: "camera_path" | "object_path" | "character_animation" | string;
  entityId?: string;
  motionId?: string;
  name?: string;
  startSec?: number;
  durationSec?: number;
  points?: number[][];
  lookAt?: number[];
  orient?: "follow" | "keep";
  enabled?: boolean;
};
type SceneMotion = {
  id: string;
  source?: string;
  loop?: boolean;
  cycleMs?: number;
};

const POSES: Array<{ id: Pose; label: string }> = [
  { id: "stand", label: "站立" },
  { id: "tpose", label: "T Pose" },
  { id: "walk", label: "行走" },
  { id: "run", label: "奔跑" },
  { id: "jump", label: "跳跃" },
  { id: "sit", label: "坐下" },
  { id: "squat", label: "深蹲" },
  { id: "kneel", label: "跪姿" },
  { id: "lie", label: "躺卧" },
  { id: "drive", label: "驾驶" },
  { id: "wave", label: "挥手" },
  { id: "hands_up", label: "举手" },
  { id: "bow", label: "鞠躬" },
  { id: "akimbo", label: "叉腰" },
  { id: "think", label: "思考" },
  { id: "fight", label: "格斗" },
  { id: "aim", label: "瞄准" },
  { id: "sword", label: "持剑" },
  { id: "spell", label: "施法" },
];

function samplePath(points: number[][], progress: number): THREE.Vector3 {
  if (!points.length) return new THREE.Vector3();
  if (points.length === 1) return new THREE.Vector3().fromArray(points[0]!);
  const scaled = Math.max(0, Math.min(1, progress)) * (points.length - 1);
  const index = Math.min(points.length - 2, Math.floor(scaled));
  const local = scaled - index;
  return new THREE.Vector3()
    .fromArray(points[index]!)
    .lerp(new THREE.Vector3().fromArray(points[index + 1]!), local);
}

function motionPoseAt(
  motionId: string,
  motion: SceneMotion | undefined,
  elapsedSec: number,
): Pose {
  const builtin = POSES.find((item) => motionId.startsWith(item.id));
  if (builtin) return builtin.id;
  const source = motion?.source ?? "";
  const cycleMs = Math.max(1, motion?.cycleMs ?? 1000);
  const atMs = motion?.loop ? (elapsedSec * 1000) % cycleMs : elapsedSec * 1000;
  let result: Pose = "stand";
  for (const line of source.split(/\r?\n/)) {
    const match = line
      .trim()
      .match(/^(\d+)ms\s+(pose|step|lean|torso)\s+([\w.-]+)/i);
    if (!match || Number(match[1]) > atMs) continue;
    const command = match[2]!.toLowerCase();
    const value = match[3]!.toLowerCase();
    if (command === "pose" && POSES.some((item) => item.id === value)) {
      result = value as Pose;
    } else if (command === "step") {
      result = "walk";
    } else if (command === "lean") {
      result = "run";
    } else if (command === "torso") {
      result = "bow";
    }
  }
  return result;
}

function makeMannequin(): THREE.Group {
  const root = new THREE.Group();
  root.name = "角色A";
  const material = new THREE.MeshStandardMaterial({
    color: 0x3b82f6,
    roughness: 0.48,
    metalness: 0.05,
  });
  const jointMaterial = new THREE.MeshStandardMaterial({ color: 0x2563eb });
  const mesh = (
    geometry: THREE.BufferGeometry,
    y: number,
    materialRef = material,
  ) => {
    const item = new THREE.Mesh(geometry, materialRef);
    item.position.y = y;
    item.castShadow = true;
    root.add(item);
    return item;
  };
  mesh(new THREE.SphereGeometry(0.18, 24, 16), 1.72, jointMaterial).name =
    "head";
  mesh(new THREE.CapsuleGeometry(0.23, 0.55, 8, 16), 1.15).name = "body";
  const armGeometry = new THREE.CapsuleGeometry(0.07, 0.58, 6, 12);
  const legGeometry = new THREE.CapsuleGeometry(0.09, 0.68, 6, 12);
  for (const [name, x] of [
    ["leftArm", -0.33],
    ["rightArm", 0.33],
  ] as const) {
    const arm = mesh(armGeometry, 1.25);
    arm.name = name;
    arm.position.x = x;
  }
  for (const [name, x] of [
    ["leftLeg", -0.13],
    ["rightLeg", 0.13],
  ] as const) {
    const leg = mesh(legGeometry, 0.42);
    leg.name = name;
    leg.position.x = x;
  }
  return root;
}

function applyPose(root: THREE.Group, pose: Pose): void {
  const leftArm = root.getObjectByName("leftArm");
  const rightArm = root.getObjectByName("rightArm");
  const leftLeg = root.getObjectByName("leftLeg");
  const rightLeg = root.getObjectByName("rightLeg");
  [leftArm, rightArm, leftLeg, rightLeg].forEach((part) =>
    part?.rotation.set(0, 0, 0),
  );
  root.rotation.x = 0;
  if (pose === "tpose") {
    if (leftArm) leftArm.rotation.z = Math.PI / 2;
    if (rightArm) rightArm.rotation.z = -Math.PI / 2;
  } else if (pose === "walk" || pose === "run") {
    const amount = pose === "run" ? 0.92 : 0.55;
    if (leftArm) leftArm.rotation.x = amount;
    if (rightArm) rightArm.rotation.x = -amount;
    if (leftLeg) leftLeg.rotation.x = -amount * 0.78;
    if (rightLeg) rightLeg.rotation.x = amount * 0.78;
    root.rotation.x = pose === "run" ? 0.22 : 0;
  } else if (pose === "wave") {
    if (rightArm) rightArm.rotation.z = -2.2;
  } else if (pose === "sit" || pose === "drive") {
    root.rotation.x = -0.08;
    if (leftLeg) leftLeg.rotation.x = -1.35;
    if (rightLeg) rightLeg.rotation.x = -1.35;
    if (pose === "drive") {
      if (leftArm) leftArm.rotation.x = -1.1;
      if (rightArm) rightArm.rotation.x = -1.1;
    }
  } else if (pose === "jump" || pose === "hands_up" || pose === "spell") {
    if (leftArm) leftArm.rotation.z = 2.45;
    if (rightArm) rightArm.rotation.z = -2.45;
    if (pose === "jump") {
      if (leftLeg) leftLeg.rotation.x = -0.45;
      if (rightLeg) rightLeg.rotation.x = 0.45;
    }
  } else if (pose === "squat" || pose === "kneel") {
    root.rotation.x = 0.18;
    if (leftLeg) leftLeg.rotation.x = -1.05;
    if (rightLeg) rightLeg.rotation.x = pose === "kneel" ? -1.55 : -1.05;
  } else if (pose === "lie") {
    root.rotation.x = Math.PI / 2;
  } else if (pose === "bow") {
    root.rotation.x = 0.72;
  } else if (pose === "akimbo") {
    if (leftArm) leftArm.rotation.z = 0.9;
    if (rightArm) rightArm.rotation.z = -0.9;
  } else if (pose === "think") {
    if (rightArm) rightArm.rotation.z = -2.15;
    if (rightArm) rightArm.rotation.x = -0.7;
  } else if (pose === "fight") {
    if (leftArm) leftArm.rotation.x = -1.25;
    if (rightArm) rightArm.rotation.x = -0.8;
    if (leftLeg) leftLeg.rotation.x = 0.35;
  } else if (pose === "aim" || pose === "sword") {
    if (leftArm) leftArm.rotation.x = -1.45;
    if (rightArm) rightArm.rotation.x = -1.45;
  }
}

function makeDeclarativeModel(model: ModelEntity): THREE.Group {
  const root = new THREE.Group();
  root.name = model.name;
  root.userData.entityId = model.id;
  for (const part of model.parts) {
    const [width = 1, height = 1, depth = 1] = part.size;
    let geometry: THREE.BufferGeometry;
    if (part.shape === "sphere") {
      geometry = new THREE.SphereGeometry(
        Math.max(width, height, depth) / 2,
        28,
        18,
      );
    } else if (part.shape === "cylinder") {
      geometry = new THREE.CylinderGeometry(
        Math.max(width, depth) / 2,
        Math.max(width, depth) / 2,
        height,
        28,
      );
    } else if (part.shape === "cone") {
      geometry = new THREE.ConeGeometry(Math.max(width, depth) / 2, height, 28);
    } else {
      geometry = new THREE.BoxGeometry(width, height, depth);
    }
    const material = new THREE.MeshStandardMaterial({
      color: new THREE.Color(part.color),
      metalness: part.metalness ?? 0,
      roughness: part.roughness ?? 0.55,
    });
    const mesh = new THREE.Mesh(geometry, material);
    mesh.name = part.name;
    mesh.position.fromArray(part.position);
    mesh.rotation.set(
      THREE.MathUtils.degToRad(part.rotation[0] ?? 0),
      THREE.MathUtils.degToRad(part.rotation[1] ?? 0),
      THREE.MathUtils.degToRad(part.rotation[2] ?? 0),
    );
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    root.add(mesh);
  }
  root.position.fromArray(model.position ?? [0, 0, 0]);
  const rotation = model.rotation ?? [0, 0, 0];
  root.rotation.set(
    THREE.MathUtils.degToRad(rotation[0] ?? 0),
    THREE.MathUtils.degToRad(rotation[1] ?? 0),
    THREE.MathUtils.degToRad(rotation[2] ?? 0),
  );
  root.scale.fromArray(model.scale ?? [1, 1, 1]);
  root.userData.basePosition = root.position.toArray();
  root.userData.baseRotationY = root.rotation.y;
  return root;
}

function makeProp(prop: PropEntity): THREE.Group {
  const root = new THREE.Group();
  root.name = prop.name;
  root.userData.entityId = prop.id;
  const [width = 1, height = 1, depth = 1] = prop.size;
  let geometry: THREE.BufferGeometry;
  if (prop.shape === "sphere") {
    geometry = new THREE.SphereGeometry(0.5, 24, 16);
  } else if (prop.shape === "cylinder") {
    geometry = new THREE.CylinderGeometry(0.5, 0.5, 1, 24);
  } else if (prop.shape === "cone") {
    geometry = new THREE.ConeGeometry(0.5, 1, 24);
  } else {
    geometry = new THREE.BoxGeometry(1, 1, 1);
  }
  const mesh = new THREE.Mesh(
    geometry,
    new THREE.MeshStandardMaterial({
      color: new THREE.Color(prop.color),
      roughness: 0.62,
      metalness:
        prop.assetId === "car" || prop.assetId === "barrel" ? 0.2 : 0.02,
    }),
  );
  mesh.scale.set(width, height, depth);
  mesh.position.y = height / 2;
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  root.add(mesh);
  root.position.fromArray(prop.position ?? [0, 0, 0]);
  const rotation = prop.rotation ?? [0, 0, 0];
  root.rotation.set(
    THREE.MathUtils.degToRad(rotation[0] ?? 0),
    THREE.MathUtils.degToRad(rotation[1] ?? 0),
    THREE.MathUtils.degToRad(rotation[2] ?? 0),
  );
  root.scale.fromArray(prop.scale ?? [1, 1, 1]);
  root.userData.basePosition = root.position.toArray();
  root.userData.baseRotationY = root.rotation.y;
  return root;
}

export function DirectorStage({
  onClose,
  sceneId,
}: {
  onClose: () => void;
  sceneId: string;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const threeSceneRef = useRef<THREE.Scene | null>(null);
  const sceneRootRef = useRef<THREE.Group | null>(null);
  const backgroundMeshRef = useRef<THREE.Mesh | null>(null);
  const roleLabelRef = useRef<HTMLDivElement | null>(null);
  const characterRef = useRef<THREE.Group | null>(null);
  const modelLayerRef = useRef<THREE.Group | null>(null);
  const propLayerRef = useRef<THREE.Group | null>(null);
  const saveTimerRef = useRef<number | null>(null);
  const snapshotTimerRef = useRef<number | null>(null);
  const skipSaveRef = useRef(true);
  const orbitRef = useRef({ theta: 0.55, phi: 1.05, radius: 5.2 });
  const tracksRef = useRef<TimelineTrack[]>([]);
  const motionsRef = useRef<SceneMotion[]>([]);
  const playheadRef = useRef(0);
  const playingRef = useRef(false);
  const durationRef = useRef(0);
  const poseRef = useRef<Pose>("stand");
  const showRoleLabelsRef = useRef(true);
  const [sceneOpen, setSceneOpen] = useState(false);
  const [pose, setPose] = useState<Pose>("stand");
  const [bodyType, setBodyType] = useState<BodyType>("mannequin");
  const [selected, setSelected] = useState<
    "scene" | "character" | "model" | "prop"
  >("scene");
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  const [models, setModels] = useState<ModelEntity[]>([]);
  const [props, setProps] = useState<PropEntity[]>([]);
  const [selectedPropId, setSelectedPropId] = useState<string | null>(null);
  const [sky, setSky] = useState("#fafafa");
  const [sceneScale, setSceneScale] = useState(100);
  const [timelineZoom, setTimelineZoom] = useState(100);
  const [scenePosition, setScenePosition] = useState({ x: 0, y: 0, z: 0 });
  const [sceneRotation, setSceneRotation] = useState({ x: 0, y: 0, z: 0 });
  const [backgroundMode, setBackgroundMode] = useState<"panorama" | "flat">(
    "panorama",
  );
  const [backgroundImage, setBackgroundImage] = useState<string | null>(null);
  const [backgroundImageName, setBackgroundImageName] = useState("");
  const [horizontalRotation, setHorizontalRotation] = useState(0);
  const [sphereRadius, setSphereRadius] = useState(90);
  const [showRoleLabels, setShowRoleLabels] = useState(true);
  const [position, setPosition] = useState({ x: 0, y: 0, z: 0 });
  const [hasPath, setHasPath] = useState(false);
  const [tracks, setTracks] = useState<TimelineTrack[]>([]);
  const [motions, setMotions] = useState<SceneMotion[]>([]);
  const [durationSec, setDurationSec] = useState(0);
  const [playheadSec, setPlayheadSec] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const [hydrated, setHydrated] = useState(false);
  const [saveState, setSaveState] = useState<
    "loading" | "saved" | "saving" | "error"
  >("loading");
  const [snapshotState, setSnapshotState] = useState<
    "idle" | "syncing" | "saved" | "error"
  >("idle");
  const hasCameraPath = tracks.some((track) => track.type === "camera_path");

  useEffect(() => {
    const controller = new AbortController();
    skipSaveRef.current = true;
    setHydrated(false);
    setSaveState("loading");
    fetch(
      `${getBackendBaseURL()}/api/plugins/director-stage/scenes/${encodeURIComponent(sceneId)}?view=full`,
      { signal: controller.signal },
    )
      .then(async (response) => {
        if (!response.ok) throw new Error(`load failed: ${response.status}`);
        return (await response.json()) as {
          scene?: {
            skyColor?: string;
            scale?: number[];
            position?: number[];
            rotation?: number[];
            backgroundMode?: "panorama" | "flat";
            backgroundImage?: string | null;
            backgroundImageName?: string | null;
            horizontalRotation?: number;
            sphereRadius?: number;
            showRoleLabels?: boolean;
          };
          entities?: Array<{
            id?: string;
            type?: string;
            name?: string;
            pose?: Pose;
            bodyType?: BodyType;
            position?: number[];
            rotation?: number[];
            scale?: number[];
            parts?: ModelPart[];
            assetId?: string;
            shape?: PropEntity["shape"];
            size?: number[];
            color?: string;
          }>;
          timeline?: { durationSec?: number; tracks?: TimelineTrack[] };
          motions?: SceneMotion[];
        };
      })
      .then((payload) => {
        const character = payload.entities?.find(
          (entity) => entity.type === "character",
        );
        if (payload.scene?.skyColor) setSky(payload.scene.skyColor);
        if (payload.scene?.scale?.length === 3)
          setSceneScale(Math.round(Number(payload.scene.scale[0]) * 100));
        if (payload.scene?.position?.length === 3)
          setScenePosition({
            x: Number(payload.scene.position[0]),
            y: Number(payload.scene.position[1]),
            z: Number(payload.scene.position[2]),
          });
        if (payload.scene?.rotation?.length === 3)
          setSceneRotation({
            x: Number(payload.scene.rotation[0]),
            y: Number(payload.scene.rotation[1]),
            z: Number(payload.scene.rotation[2]),
          });
        if (payload.scene?.backgroundMode)
          setBackgroundMode(payload.scene.backgroundMode);
        setBackgroundImage(payload.scene?.backgroundImage ?? null);
        setBackgroundImageName(payload.scene?.backgroundImageName ?? "");
        setHorizontalRotation(Number(payload.scene?.horizontalRotation ?? 0));
        setSphereRadius(Number(payload.scene?.sphereRadius ?? 90));
        setShowRoleLabels(payload.scene?.showRoleLabels !== false);
        if (character?.pose && POSES.some((item) => item.id === character.pose))
          setPose(character.pose);
        if (character?.bodyType) setBodyType(character.bodyType);
        if (character?.position?.length === 3) {
          setPosition({
            x: Number(character.position[0]),
            y: Number(character.position[1]),
            z: Number(character.position[2]),
          });
        }
        setModels(
          (payload.entities ?? []).filter(
            (entity): entity is ModelEntity =>
              entity.type === "model" &&
              typeof entity.id === "string" &&
              typeof entity.name === "string" &&
              Array.isArray(entity.parts),
          ),
        );
        setProps(
          (payload.entities ?? []).filter(
            (entity): entity is PropEntity =>
              entity.type === "prop" &&
              typeof entity.id === "string" &&
              typeof entity.name === "string" &&
              typeof entity.assetId === "string" &&
              typeof entity.shape === "string" &&
              Array.isArray(entity.size) &&
              typeof entity.color === "string",
          ),
        );
        const loadedTracks = payload.timeline?.tracks ?? [];
        const loadedMotions = payload.motions ?? [];
        const loadedDuration = Number(payload.timeline?.durationSec ?? 0);
        setTracks(loadedTracks);
        setMotions(loadedMotions);
        setDurationSec(loadedDuration);
        setHasPath(Boolean(loadedTracks.length));
        setPlayheadSec(0);
        setHydrated(true);
        setSaveState("saved");
      })
      .catch((error: unknown) => {
        if ((error as { name?: string }).name !== "AbortError") {
          setSaveState("error");
        }
      });
    return () => controller.abort();
  }, [refreshToken, sceneId]);

  useEffect(() => {
    tracksRef.current = tracks;
    motionsRef.current = motions;
    durationRef.current = durationSec;
  }, [durationSec, motions, tracks]);

  useEffect(() => {
    playheadRef.current = playheadSec;
  }, [playheadSec]);

  useEffect(() => {
    playingRef.current = playing;
  }, [playing]);

  useEffect(() => {
    poseRef.current = pose;
  }, [pose]);

  useEffect(() => {
    showRoleLabelsRef.current = showRoleLabels;
  }, [showRoleLabels]);

  useEffect(() => {
    if (!hydrated) return;
    if (skipSaveRef.current) {
      skipSaveRef.current = false;
      return;
    }
    setSaveState("saving");
    const timer = window.setTimeout(() => {
      fetch(
        `${getBackendBaseURL()}/api/plugins/director-stage/scenes/${encodeURIComponent(sceneId)}/edit`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            operations: [
              {
                type: "set_scene",
                skyColor: sky,
                scale: [sceneScale / 100, sceneScale / 100, sceneScale / 100],
                position: [scenePosition.x, scenePosition.y, scenePosition.z],
                rotation: [sceneRotation.x, sceneRotation.y, sceneRotation.z],
              },
              {
                type: "set_environment",
                skyColor: sky,
                backgroundMode,
                backgroundImage,
                backgroundImageName,
                horizontalRotation,
                sphereRadius,
                showRoleLabels,
              },
              {
                type: "set_pose",
                entityId: "character-1",
                pose,
                bodyType,
              },
              {
                type: "set_transform",
                entityId: "character-1",
                position: [position.x, position.y, position.z],
              },
            ],
          }),
        },
      )
        .then((response) => {
          if (!response.ok) throw new Error(`save failed: ${response.status}`);
          setSaveState("saved");
        })
        .catch(() => setSaveState("error"));
    }, 450);
    saveTimerRef.current = timer;
    return () => {
      window.clearTimeout(timer);
      if (saveTimerRef.current === timer) saveTimerRef.current = null;
    };
  }, [
    backgroundImage,
    backgroundImageName,
    backgroundMode,
    bodyType,
    horizontalRotation,
    hydrated,
    pose,
    position,
    sceneId,
    scenePosition,
    sceneRotation,
    sceneScale,
    showRoleLabels,
    sky,
    sphereRadius,
  ]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const scene = new THREE.Scene();
    threeSceneRef.current = scene;
    const sceneRoot = new THREE.Group();
    sceneRoot.name = "场景根节点";
    sceneRootRef.current = sceneRoot;
    scene.add(sceneRoot);
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
    const renderer = new THREE.WebGLRenderer({
      antialias: true,
      preserveDrawingBuffer: true,
    });
    renderer.setClearColor(new THREE.Color("#fafafa"), 1);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    rendererRef.current = renderer;
    host.appendChild(renderer.domElement);

    scene.add(new THREE.HemisphereLight(0xffffff, 0x64748b, 2.4));
    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(3, 6, 4);
    key.castShadow = true;
    scene.add(key);
    const grid = new THREE.GridHelper(16, 32, 0xb7c4d6, 0xdfe6ef);
    sceneRoot.add(grid);
    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(16, 16),
      new THREE.ShadowMaterial({ color: 0x111827, opacity: 0.08 }),
    );
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    sceneRoot.add(floor);
    const character = makeMannequin();
    character.userData.entityId = "character-1";
    character.userData.basePosition = [0, 0, 0];
    character.userData.baseRotationY = 0;
    characterRef.current = character;
    sceneRoot.add(character);
    const modelLayer = new THREE.Group();
    modelLayer.name = "程序化模型";
    modelLayerRef.current = modelLayer;
    sceneRoot.add(modelLayer);
    const propLayer = new THREE.Group();
    propLayer.name = "场景道具";
    propLayerRef.current = propLayer;
    sceneRoot.add(propLayer);

    const updateCamera = () => {
      const { theta, phi, radius } = orbitRef.current;
      camera.position.set(
        radius * Math.sin(phi) * Math.sin(theta),
        radius * Math.cos(phi) + 1,
        radius * Math.sin(phi) * Math.cos(theta),
      );
      camera.lookAt(0, 0.9, 0);
    };
    updateCamera();
    let drag: { x: number; y: number } | null = null;
    const down = (event: PointerEvent) => {
      drag = { x: event.clientX, y: event.clientY };
    };
    const move = (event: PointerEvent) => {
      if (!drag) return;
      orbitRef.current.theta -= (event.clientX - drag.x) * 0.008;
      orbitRef.current.phi = Math.max(
        0.35,
        Math.min(1.48, orbitRef.current.phi + (event.clientY - drag.y) * 0.006),
      );
      drag = { x: event.clientX, y: event.clientY };
      updateCamera();
    };
    const up = () => {
      drag = null;
    };
    renderer.domElement.addEventListener("pointerdown", down);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);

    const resize = () => {
      const width = host.clientWidth;
      const height = host.clientHeight;
      camera.aspect = Math.max(1, width) / Math.max(1, height);
      camera.updateProjectionMatrix();
      renderer.setSize(width, height, false);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    resize();
    const findEntityObject = (entityId: string): THREE.Group | null => {
      if (character.userData.entityId === entityId) return character;
      const candidates = [
        ...modelLayer.children,
        ...propLayer.children,
      ] as THREE.Group[];
      return (
        candidates.find((item) => item.userData.entityId === entityId) ?? null
      );
    };
    const evaluateTimeline = (atSec: number): boolean => {
      const allTracks = tracksRef.current;
      const animatedIds = new Set(
        allTracks
          .filter((track) => track.type === "object_path" && track.entityId)
          .map((track) => track.entityId as string),
      );
      for (const entityId of animatedIds) {
        const object = findEntityObject(entityId);
        if (!object) continue;
        object.position.fromArray(object.userData.basePosition ?? [0, 0, 0]);
        object.rotation.y = Number(object.userData.baseRotationY ?? 0);
      }
      applyPose(character, poseRef.current);
      let cameraDriven = false;
      for (const track of allTracks) {
        if (track.enabled === false) continue;
        const start = Number(track.startSec ?? 0);
        const duration = Math.max(0.001, Number(track.durationSec ?? 0));
        if (atSec < start || atSec > start + duration) continue;
        const progress = (atSec - start) / duration;
        if (track.type === "camera_path" && (track.points?.length ?? 0) >= 2) {
          camera.position.copy(samplePath(track.points ?? [], progress));
          camera.lookAt(
            new THREE.Vector3().fromArray(track.lookAt ?? [0, 0.9, 0]),
          );
          cameraDriven = true;
        } else if (
          track.type === "object_path" &&
          track.entityId &&
          (track.points?.length ?? 0) >= 2
        ) {
          const object = findEntityObject(track.entityId);
          if (!object) continue;
          object.position.copy(samplePath(track.points ?? [], progress));
          if (track.orient !== "keep") {
            const ahead = samplePath(
              track.points ?? [],
              Math.min(1, progress + 0.01),
            );
            const delta = ahead.sub(object.position);
            if (delta.lengthSq() > 0.000001)
              object.rotation.y = Math.atan2(delta.x, delta.z);
          }
        } else if (
          track.type === "character_animation" &&
          track.motionId &&
          (!track.entityId || track.entityId === "character-1")
        ) {
          const motion = motionsRef.current.find(
            (item) => item.id === track.motionId,
          );
          applyPose(
            character,
            motionPoseAt(track.motionId, motion, atSec - start),
          );
          if (
            track.motionId.startsWith("walk") ||
            track.motionId.startsWith("run")
          ) {
            const amount = track.motionId.startsWith("run") ? 0.92 : 0.55;
            const swing = Math.sin(progress * Math.PI * 8) * amount;
            const leftArm = character.getObjectByName("leftArm");
            const rightArm = character.getObjectByName("rightArm");
            const leftLeg = character.getObjectByName("leftLeg");
            const rightLeg = character.getObjectByName("rightLeg");
            if (leftArm) leftArm.rotation.x = swing;
            if (rightArm) rightArm.rotation.x = -swing;
            if (leftLeg) leftLeg.rotation.x = -swing * 0.78;
            if (rightLeg) rightLeg.rotation.x = swing * 0.78;
          }
        }
      }
      return cameraDriven;
    };
    let frame = 0;
    let previous = performance.now();
    let lastUiSync = previous;
    const render = (now = performance.now()) => {
      const elapsed = Math.min(0.1, Math.max(0, (now - previous) / 1000));
      previous = now;
      if (playingRef.current && durationRef.current > 0) {
        playheadRef.current += elapsed;
        if (playheadRef.current >= durationRef.current)
          playheadRef.current %= durationRef.current;
        if (now - lastUiSync > 80) {
          lastUiSync = now;
          setPlayheadSec(playheadRef.current);
        }
      }
      const cameraDriven = evaluateTimeline(playheadRef.current);
      if (!cameraDriven) updateCamera();
      renderer.render(scene, camera);
      const label = roleLabelRef.current;
      if (label) {
        const point = character.getWorldPosition(new THREE.Vector3());
        point.y += 1.9;
        point.project(camera);
        const visible =
          showRoleLabelsRef.current && point.z > -1 && point.z < 1;
        label.style.display = visible ? "block" : "none";
        if (visible) {
          label.style.left = `${((point.x + 1) / 2) * host.clientWidth}px`;
          label.style.top = `${((-point.y + 1) / 2) * host.clientHeight}px`;
        }
      }
      frame = requestAnimationFrame(render);
    };
    render();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      renderer.domElement.removeEventListener("pointerdown", down);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      renderer.dispose();
      threeSceneRef.current = null;
      sceneRootRef.current = null;
      backgroundMeshRef.current = null;
      modelLayerRef.current = null;
      propLayerRef.current = null;
      renderer.domElement.remove();
    };
  }, []);

  useEffect(() => {
    const layer = modelLayerRef.current;
    if (!layer) return;
    for (const child of [...layer.children]) {
      child.traverse((object) => {
        if (!(object instanceof THREE.Mesh)) return;
        object.geometry.dispose();
        const materials = Array.isArray(object.material)
          ? object.material
          : [object.material];
        materials.forEach((material) => material.dispose());
      });
      layer.remove(child);
    }
    models.forEach((model) => layer.add(makeDeclarativeModel(model)));
  }, [models]);

  useEffect(() => {
    const layer = propLayerRef.current;
    if (!layer) return;
    for (const child of [...layer.children]) {
      child.traverse((object) => {
        if (!(object instanceof THREE.Mesh)) return;
        object.geometry.dispose();
        const materials = Array.isArray(object.material)
          ? object.material
          : [object.material];
        materials.forEach((material) => material.dispose());
      });
      layer.remove(child);
    }
    props.forEach((prop) => layer.add(makeProp(prop)));
  }, [props]);

  useEffect(() => {
    const character = characterRef.current;
    if (!character) return;
    character.position.set(position.x, position.y, position.z);
    character.userData.basePosition = [position.x, position.y, position.z];
    const scale =
      bodyType === "child" ? 0.72 : bodyType === "female" ? 0.94 : 1;
    character.scale.set(
      bodyType === "female" ? scale * 0.88 : scale,
      scale,
      bodyType === "female" ? scale * 0.88 : scale,
    );
    applyPose(character, pose);
  }, [bodyType, pose, position]);

  useEffect(() => {
    const root = sceneRootRef.current;
    if (!root) return;
    const scale = Math.max(0.5, Math.min(1.6, sceneScale / 100));
    root.scale.setScalar(scale);
    root.position.set(scenePosition.x, scenePosition.y, scenePosition.z);
    root.rotation.set(
      THREE.MathUtils.degToRad(sceneRotation.x),
      THREE.MathUtils.degToRad(sceneRotation.y),
      THREE.MathUtils.degToRad(sceneRotation.z),
    );
  }, [scenePosition, sceneRotation, sceneScale]);

  useEffect(() => {
    const scene = threeSceneRef.current;
    if (!scene) return;
    const removeBackgroundMesh = () => {
      const mesh = backgroundMeshRef.current;
      if (!mesh) return;
      scene.remove(mesh);
      mesh.geometry.dispose();
      const material = mesh.material as THREE.MeshBasicMaterial;
      material.map?.dispose();
      material.dispose();
      backgroundMeshRef.current = null;
    };
    removeBackgroundMesh();
    scene.background = new THREE.Color(sky);
    if (!backgroundImage) return;
    let cancelled = false;
    const texture = new THREE.TextureLoader().load(backgroundImage, () => {
      if (cancelled) {
        texture.dispose();
        return;
      }
      texture.colorSpace = THREE.SRGBColorSpace;
      if (backgroundMode === "flat") {
        scene.background = texture;
        return;
      }
      texture.mapping = THREE.EquirectangularReflectionMapping;
      const mesh = new THREE.Mesh(
        new THREE.SphereGeometry(sphereRadius, 48, 24),
        new THREE.MeshBasicMaterial({ map: texture, side: THREE.BackSide }),
      );
      mesh.rotation.y = THREE.MathUtils.degToRad(horizontalRotation);
      mesh.name = "全景背景";
      backgroundMeshRef.current = mesh;
      scene.add(mesh);
    });
    return () => {
      cancelled = true;
      if (scene.background === texture) scene.background = new THREE.Color(sky);
      removeBackgroundMesh();
    };
  }, [backgroundImage, backgroundMode, horizontalRotation, sky, sphereRadius]);

  useEffect(() => {
    const renderer = rendererRef.current;
    if (renderer) renderer.setClearColor(new THREE.Color(sky), 1);
  }, [sky]);

  useEffect(() => {
    if (!hydrated || !rendererRef.current || playing) return;
    if (snapshotTimerRef.current !== null)
      window.clearTimeout(snapshotTimerRef.current);
    setSnapshotState("syncing");
    const timer = window.setTimeout(() => {
      const canvas = rendererRef.current?.domElement;
      if (!canvas || canvas.width < 64 || canvas.height < 64) {
        setSnapshotState("error");
        return;
      }
      const dataUrl = canvas.toDataURL("image/png");
      fetch(
        `${getBackendBaseURL()}/api/plugins/director-stage/scenes/${encodeURIComponent(sceneId)}/visual-snapshot`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ data_url: dataUrl, view: "director" }),
        },
      )
        .then((response) => {
          if (!response.ok)
            throw new Error(`snapshot failed: ${response.status}`);
          setSnapshotState("saved");
        })
        .catch(() => setSnapshotState("error"));
    }, 320);
    snapshotTimerRef.current = timer;
    return () => {
      window.clearTimeout(timer);
      if (snapshotTimerRef.current === timer) snapshotTimerRef.current = null;
    };
  }, [
    bodyType,
    backgroundImage,
    backgroundMode,
    hydrated,
    horizontalRotation,
    models,
    playheadSec,
    playing,
    pose,
    position,
    props,
    sceneId,
    scenePosition,
    sceneRotation,
    sceneScale,
    showRoleLabels,
    sky,
    sphereRadius,
    tracks,
  ]);

  const exportFrame = () => {
    const data = rendererRef.current?.domElement.toDataURL("image/png");
    if (!data) return;
    const anchor = document.createElement("a");
    anchor.href = data;
    anchor.download = "echo-director-stage.png";
    anchor.click();
  };

  const stepHistory = (action: "undo" | "redo") => {
    setSaveState("loading");
    setPlaying(false);
    fetch(
      `${getBackendBaseURL()}/api/plugins/director-stage/scenes/${encodeURIComponent(sceneId)}/history`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, steps: 1 }),
      },
    )
      .then((response) => {
        if (!response.ok) throw new Error(`history failed: ${response.status}`);
        setRefreshToken((value) => value + 1);
      })
      .catch(() => setSaveState("error"));
  };

  const addCameraPath = () => {
    const points = [
      [0, 1.6, 5],
      [3.5, 2.1, 2.8],
      [0, 1.6, -5],
    ];
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    setSaveState("saving");
    fetch(
      `${getBackendBaseURL()}/api/plugins/director-stage/scenes/${encodeURIComponent(sceneId)}/edit`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          operations: [
            { type: "set_scene", skyColor: sky },
            {
              type: "set_pose",
              entityId: "character-1",
              pose,
              bodyType,
            },
            {
              type: "set_transform",
              entityId: "character-1",
              position: [position.x, position.y, position.z],
            },
            {
              type: "add_camera_path",
              cameraId: "camera-1",
              name: "环绕运镜",
              points,
              durationSec: 5,
            },
          ],
        }),
      },
    )
      .then(async (response) => {
        if (!response.ok) throw new Error(`save failed: ${response.status}`);
        const payload = (await response.json()) as {
          results?: Array<{ trackId?: string }>;
        };
        const trackId = payload.results?.find((item) => item.trackId)?.trackId;
        setTracks((current) => [
          ...current,
          {
            id: trackId ?? `track-camera-${Date.now()}`,
            type: "camera_path",
            entityId: "camera-1",
            name: "环绕运镜",
            startSec: 0,
            durationSec: 5,
            points,
            lookAt: [0, 0.9, 0],
          },
        ]);
        setDurationSec((current) => Math.max(current, 5));
        setHasPath(true);
        setSaveState("saved");
      })
      .catch(() => setSaveState("error"));
  };

  const addCharacterAnimation = () => {
    setSaveState("saving");
    fetch(
      `${getBackendBaseURL()}/api/plugins/director-stage/scenes/${encodeURIComponent(sceneId)}/edit`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          operations: [
            {
              type: "add_animation_clip",
              characterId: "character-1",
              motionId: "walk",
              name: "行走循环",
              startSec: 0,
              durationSec: 3,
            },
          ],
        }),
      },
    )
      .then(async (response) => {
        if (!response.ok) throw new Error(`save failed: ${response.status}`);
        const payload = (await response.json()) as {
          results?: Array<{ trackId?: string }>;
        };
        const trackId = payload.results?.find((item) => item.trackId)?.trackId;
        setTracks((current) => [
          ...current,
          {
            id: trackId ?? `track-animation-${Date.now()}`,
            type: "character_animation",
            entityId: "character-1",
            motionId: "walk",
            name: "行走循环",
            startSec: 0,
            durationSec: 3,
            enabled: true,
          },
        ]);
        setDurationSec((current) => Math.max(current, 3));
        setHasPath(true);
        setSaveState("saved");
      })
      .catch(() => setSaveState("error"));
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <div className="flex h-11 shrink-0 items-center border-b border-border-subtle px-2">
        <Button
          variant="ghost"
          size="sm"
          className={cn(
            "h-8 gap-1.5 text-[11px]",
            sceneOpen &&
              "bg-foreground text-background hover:bg-foreground hover:text-background",
          )}
          onClick={() => setSceneOpen((value) => !value)}
        >
          <BoxIcon className="size-3.5" />
          场景
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="h-8 bg-foreground text-[11px] text-background hover:bg-foreground hover:text-background"
        >
          导演视角
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="h-8 text-[11px] text-muted-foreground"
        >
          机位视角
        </Button>
        <span className="mx-2 h-5 w-px bg-border-subtle" />
        <Button
          variant="ghost"
          size="icon"
          className="size-8"
          title="虚拟摄像机（手机扫码运镜）"
        >
          <SmartphoneIcon className="size-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="size-8"
          title="快捷键一览"
        >
          <KeyboardIcon className="size-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="size-8"
          onClick={() => stepHistory("undo")}
          disabled={saveState === "loading" || saveState === "saving"}
        >
          <Undo2Icon className="size-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="size-8"
          onClick={() => stepHistory("redo")}
          disabled={saveState === "loading" || saveState === "saving"}
        >
          <Redo2Icon className="size-3.5" />
        </Button>
        <span className="flex-1" />
        <span className="sr-only" aria-live="polite">
          {snapshotState === "syncing"
            ? "同步画面中"
            : snapshotState === "saved"
              ? "画面已同步"
              : snapshotState === "error"
                ? "画面同步失败"
                : null}
        </span>
        <Button
          variant="ghost"
          size="sm"
          className="h-8 gap-1.5 text-[11px]"
          onClick={exportFrame}
        >
          <DownloadIcon className="size-3.5" />
          导出图片
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="h-8 text-[11px] text-muted-foreground"
          disabled
        >
          导出运镜视频
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="h-8 gap-1.5 text-[11px]"
          onClick={onClose}
        >
          <XIcon className="size-3.5" />
          退出编辑
        </Button>
      </div>
      <div className="flex min-h-0 flex-1">
        {sceneOpen ? (
          <aside className="w-[220px] shrink-0 border-r border-border-subtle bg-background p-3">
            <div className="flex items-center">
              <span className="text-xs font-semibold">场景</span>
              <span className="flex-1" />
              <ChevronLeftIcon className="size-3.5 text-muted-foreground" />
            </div>
            <Input
              className="mt-3 h-8 text-[10px]"
              placeholder="搜索场景内容"
            />
            <div className="mt-4 text-[9px] text-muted-foreground">相机　1</div>
            <button className="mt-1 flex w-full items-center gap-2 rounded-lg px-2 py-2 text-[11px] hover:bg-muted">
              <CameraIcon className="size-3.5" />
              机位1
            </button>
            <div className="mt-3 text-[9px] text-muted-foreground">角色　1</div>
            <button
              onClick={() => setSelected("character")}
              className={cn(
                "mt-1 flex w-full items-center gap-2 rounded-lg px-2 py-2 text-[11px]",
                selected === "character" ? "bg-muted" : "hover:bg-muted",
              )}
            >
              <UserRoundIcon className="size-3.5 text-blue-500" />
              角色A
            </button>
            {props.length ? (
              <>
                <div className="mt-3 text-[9px] text-muted-foreground">
                  场景道具　{props.length}
                </div>
                {props.map((prop) => (
                  <button
                    key={prop.id}
                    onClick={() => {
                      setSelected("prop");
                      setSelectedPropId(prop.id);
                    }}
                    className={cn(
                      "mt-1 flex w-full items-center gap-2 rounded-lg px-2 py-2 text-[11px]",
                      selected === "prop" && selectedPropId === prop.id
                        ? "bg-muted"
                        : "hover:bg-muted",
                    )}
                  >
                    <BoxIcon className="size-3.5 text-amber-600" />
                    <span className="truncate">{prop.name}</span>
                    <span className="ml-auto text-[9px] text-muted-foreground">
                      {prop.assetId}
                    </span>
                  </button>
                ))}
              </>
            ) : null}
            {models.length ? (
              <>
                <div className="mt-3 text-[9px] text-muted-foreground">
                  程序化模型　{models.length}
                </div>
                {models.map((model) => (
                  <button
                    key={model.id}
                    onClick={() => {
                      setSelected("model");
                      setSelectedModelId(model.id);
                    }}
                    className={cn(
                      "mt-1 flex w-full items-center gap-2 rounded-lg px-2 py-2 text-[11px]",
                      selected === "model" && selectedModelId === model.id
                        ? "bg-muted"
                        : "hover:bg-muted",
                    )}
                  >
                    <BoxIcon className="size-3.5 text-violet-500" />
                    <span className="truncate">{model.name}</span>
                    <span className="ml-auto text-[9px] text-muted-foreground">
                      {model.parts.length}
                    </span>
                  </button>
                ))}
              </>
            ) : null}
          </aside>
        ) : null}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="relative min-h-0 flex-1 bg-[#fafafa]" ref={hostRef}>
            <div
              ref={roleLabelRef}
              className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-md border border-blue-200 bg-background/95 px-2 py-1 text-[9px] font-medium text-blue-700 shadow-sm backdrop-blur-sm"
            >
              角色A
            </div>
            <div className="absolute bottom-4 left-1/2 z-10 flex -translate-x-1/2 items-center gap-1 rounded-[12px] border bg-background/95 p-1 shadow-lg backdrop-blur-sm">
              <Button variant="ghost" size="icon" className="size-8">
                <Move3DIcon className="size-4" />
              </Button>
              <Button variant="ghost" size="icon" className="size-8">
                <BoxIcon className="size-4" />
              </Button>
              <Button variant="ghost" size="icon" className="size-8">
                <Globe2Icon className="size-4" />
              </Button>
              <Button variant="ghost" size="icon" className="size-8">
                <CameraIcon className="size-4" />
              </Button>
              <Button size="icon" className="size-8">
                <FootprintsIcon className="size-4" />
              </Button>
              <Button variant="ghost" size="icon" className="size-8">
                <RectangleHorizontalIcon className="size-4" />
              </Button>
              <Button variant="ghost" size="icon" className="size-8">
                <PanelRightCloseIcon className="size-4" />
              </Button>
            </div>
          </div>
          <div className="h-[230px] shrink-0 border-t border-border-subtle bg-background">
            <div className="flex h-10 items-center gap-1 border-b border-border-subtle px-3">
              <Button
                variant="outline"
                size="icon"
                className="size-7"
                onClick={() =>
                  setTimelineZoom((value) => Math.max(50, value - 25))
                }
              >
                −
              </Button>
              <span className="w-11 text-center text-[10px]">
                {timelineZoom}%
              </span>
              <Button
                variant="outline"
                size="icon"
                className="size-7"
                onClick={() =>
                  setTimelineZoom((value) => Math.min(300, value + 25))
                }
              >
                ＋
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="ml-2 size-7"
                disabled={!hasPath}
                onClick={() => setPlaying((value) => !value)}
              >
                {playing ? (
                  <PauseIcon className="size-3.5" />
                ) : (
                  <CirclePlayIcon className="size-3.5" />
                )}
              </Button>
              <span className="text-[10px] text-muted-foreground">
                {playheadSec.toFixed(1)}s / {durationSec.toFixed(1)}s
              </span>
              <input
                aria-label="时间线播放位置"
                type="range"
                min="0"
                max={Math.max(0.01, durationSec)}
                step="0.01"
                value={Math.min(playheadSec, Math.max(0.01, durationSec))}
                disabled={!hasPath}
                onChange={(event) => {
                  const value = Number(event.target.value);
                  setPlaying(false);
                  playheadRef.current = value;
                  setPlayheadSec(value);
                }}
                className="ml-2 w-36 accent-violet-500"
              />
              <span className="flex-1" />
              <Button
                variant="outline"
                size="sm"
                className="h-7 gap-1 text-[10px]"
                onClick={addCameraPath}
                disabled={saveState === "loading" || hasCameraPath}
              >
                <PlusIcon className="size-3" />
                添加路径
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="h-7 gap-1 text-[10px]"
                onClick={addCharacterAnimation}
              >
                <PlusIcon className="size-3" />
                角色动画
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 gap-1 text-[10px]"
              >
                <VideoIcon className="size-3" />
                手机运镜
              </Button>
            </div>
            {hasPath ? (
              <div className="h-[189px] overflow-auto px-4 py-3 text-[10px]">
                <div
                  style={{ width: `${Math.max(100, timelineZoom)}%` }}
                  className="min-w-full"
                >
                  {tracks.map((track) => {
                    const left =
                      (Math.max(0, Number(track.startSec ?? 0)) /
                        Math.max(0.01, durationSec)) *
                      100;
                    const width =
                      (Math.max(0.05, Number(track.durationSec ?? 0)) /
                        Math.max(0.01, durationSec)) *
                      100;
                    const color =
                      track.type === "camera_path"
                        ? "bg-violet-500"
                        : track.type === "character_animation"
                          ? "bg-blue-500"
                          : "bg-emerald-500";
                    return (
                      <div
                        key={track.id}
                        className="mb-2 flex items-center gap-3"
                      >
                        <span className="w-20 truncate text-muted-foreground">
                          {track.type === "camera_path"
                            ? "机位"
                            : track.type === "character_animation"
                              ? "角色动作"
                              : "对象路径"}
                        </span>
                        <div className="relative h-8 flex-1 rounded-md bg-muted/70">
                          <div
                            className={cn(
                              "absolute inset-y-1 overflow-hidden rounded px-2 py-1 text-white",
                              color,
                            )}
                            style={{
                              left: `${Math.min(100, left)}%`,
                              width: `${Math.max(0, Math.min(100 - left, width))}%`,
                            }}
                          >
                            <span className="block truncate">
                              {track.name || track.motionId || "未命名片段"} ·{" "}
                              {Number(track.durationSec ?? 0).toFixed(1)}s
                            </span>
                          </div>
                          <div
                            className="pointer-events-none absolute inset-y-0 w-px bg-foreground/70"
                            style={{
                              left: `${Math.min(
                                100,
                                (playheadSec / Math.max(0.01, durationSec)) *
                                  100,
                              )}%`,
                            }}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : (
              <div className="grid h-[189px] place-items-center text-[10px] text-muted-foreground">
                <div className="text-center">
                  <FootprintsIcon className="mx-auto size-5 opacity-50" />
                  <p className="mt-2">点击“添加路径”创建运镜片段</p>
                </div>
              </div>
            )}
          </div>
        </div>
        <aside className="w-[286px] shrink-0 overflow-y-auto border-l border-border-subtle bg-background p-4">
          {selected === "scene" ? (
            <>
              <h3 className="text-sm font-semibold">3D场景</h3>
              <label className="mt-5 block text-[10px] text-muted-foreground">
                场景缩放
              </label>
              <input
                type="range"
                min="50"
                max="160"
                value={sceneScale}
                onChange={(event) => setSceneScale(Number(event.target.value))}
                className="mt-2 w-full accent-foreground"
              />
              <div className="mt-1 text-right text-[10px]">{sceneScale}%</div>
              <label className="mt-4 block text-[10px] text-muted-foreground">
                场景平移
              </label>
              <div className="mt-2 grid grid-cols-3 gap-2">
                {(["x", "y", "z"] as const).map((axis) => (
                  <label
                    key={axis}
                    className="flex items-center rounded-lg border px-2"
                  >
                    <span className="mr-1 text-[9px] text-muted-foreground">
                      {axis}
                    </span>
                    <input
                      type="number"
                      step="0.1"
                      value={scenePosition[axis]}
                      onChange={(event) =>
                        setScenePosition((current) => ({
                          ...current,
                          [axis]: Number(event.target.value),
                        }))
                      }
                      className="h-8 min-w-0 flex-1 bg-transparent text-[10px] outline-none"
                    />
                  </label>
                ))}
              </div>
              <label className="mt-4 block text-[10px] text-muted-foreground">
                场景旋转
              </label>
              <div className="mt-2 grid grid-cols-3 gap-2">
                {(["x", "y", "z"] as const).map((axis) => (
                  <label
                    key={axis}
                    className="flex items-center rounded-lg border px-2"
                  >
                    <span className="mr-1 text-[9px] text-muted-foreground">
                      {axis}
                    </span>
                    <input
                      type="number"
                      step="1"
                      value={sceneRotation[axis]}
                      onChange={(event) =>
                        setSceneRotation((current) => ({
                          ...current,
                          [axis]: Number(event.target.value),
                        }))
                      }
                      className="h-8 min-w-0 flex-1 bg-transparent text-[10px] outline-none"
                    />
                  </label>
                ))}
              </div>
              <label className="mt-5 block text-[10px] text-muted-foreground">
                天空颜色
              </label>
              <div className="mt-2 flex items-center gap-2 rounded-lg border p-2">
                <input
                  type="color"
                  value={sky}
                  onChange={(event) => setSky(event.target.value)}
                  className="size-6 border-0 bg-transparent"
                />
                <span className="text-[11px]">{sky.toUpperCase()}</span>
              </div>
              <label className="mt-5 block text-[10px] text-muted-foreground">
                背景图片
              </label>
              <div className="mt-2 flex items-center gap-2 rounded-lg border px-3 py-2">
                <span className="min-w-0 flex-1 truncate text-[10px]">
                  {backgroundImageName || "无"}
                </span>
                {backgroundImage ? (
                  <button
                    type="button"
                    className="text-[9px] text-muted-foreground hover:text-foreground"
                    onClick={() => {
                      setBackgroundImage(null);
                      setBackgroundImageName("");
                    }}
                  >
                    清除
                  </button>
                ) : null}
                <label className="cursor-pointer text-[9px] font-medium">
                  选择
                  <input
                    type="file"
                    accept="image/*"
                    hidden
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      event.target.value = "";
                      if (!file) return;
                      if (file.size > 2_800_000) {
                        toast.error("背景图片需小于 2.8 MB");
                        return;
                      }
                      const reader = new FileReader();
                      reader.onload = () => {
                        if (typeof reader.result !== "string") return;
                        setBackgroundImage(reader.result);
                        setBackgroundImageName(file.name);
                      };
                      reader.readAsDataURL(file);
                    }}
                  />
                </label>
              </div>
              <label className="mt-5 block text-[10px] text-muted-foreground">
                背景模式
              </label>
              <div className="mt-2 flex rounded-lg bg-muted p-1 text-[10px]">
                <button
                  onClick={() => setBackgroundMode("panorama")}
                  className={cn(
                    "flex-1 rounded-md py-1.5",
                    backgroundMode === "panorama"
                      ? "bg-foreground text-background"
                      : "text-muted-foreground",
                  )}
                >
                  全景
                </button>
                <button
                  onClick={() => setBackgroundMode("flat")}
                  className={cn(
                    "flex-1 rounded-md py-1.5",
                    backgroundMode === "flat"
                      ? "bg-foreground text-background"
                      : "text-muted-foreground",
                  )}
                >
                  平面
                </button>
              </div>
              {backgroundMode === "panorama" ? (
                <>
                  <label className="mt-4 block text-[10px] text-muted-foreground">
                    水平旋转
                  </label>
                  <div className="mt-2 flex items-center gap-3">
                    <input
                      type="range"
                      min="-180"
                      max="180"
                      value={horizontalRotation}
                      onChange={(event) =>
                        setHorizontalRotation(Number(event.target.value))
                      }
                      className="min-w-0 flex-1 accent-foreground"
                    />
                    <span className="w-12 rounded-lg border px-2 py-1 text-right text-[10px]">
                      {horizontalRotation}°
                    </span>
                  </div>
                  <label className="mt-4 block text-[10px] text-muted-foreground">
                    球形半径
                  </label>
                  <div className="mt-2 flex items-center gap-3">
                    <input
                      type="range"
                      min="30"
                      max="180"
                      value={sphereRadius}
                      onChange={(event) =>
                        setSphereRadius(Number(event.target.value))
                      }
                      className="min-w-0 flex-1 accent-foreground"
                    />
                    <span className="w-12 rounded-lg border px-2 py-1 text-right text-[10px]">
                      {sphereRadius}
                    </span>
                  </div>
                </>
              ) : null}
              <div className="mt-5 flex items-center border-t border-border-subtle pt-4">
                <span className="text-[10px] text-muted-foreground">
                  角色标签
                </span>
                <button
                  type="button"
                  role="switch"
                  aria-checked={showRoleLabels}
                  onClick={() => setShowRoleLabels((value) => !value)}
                  className={cn(
                    "ml-auto h-5 w-9 rounded-full p-0.5 transition",
                    showRoleLabels ? "bg-foreground" : "bg-muted-foreground/30",
                  )}
                >
                  <span
                    className={cn(
                      "block size-4 rounded-full bg-background transition-transform",
                      showRoleLabels && "translate-x-4",
                    )}
                  />
                </button>
              </div>
            </>
          ) : selected === "character" ? (
            <>
              <h3 className="text-sm font-semibold">角色A</h3>
              <label className="mt-5 block text-[10px] text-muted-foreground">
                角色素体
              </label>
              <div className="mt-2 grid grid-cols-3 gap-1">
                {(
                  [
                    { id: "mannequin", label: "标准男" },
                    { id: "female", label: "标准女" },
                    { id: "child", label: "儿童" },
                  ] as const
                ).map((item) => (
                  <button
                    key={item.id}
                    onClick={() => setBodyType(item.id)}
                    className={cn(
                      "rounded-lg border px-1 py-2 text-[9px]",
                      bodyType === item.id
                        ? "border-foreground bg-foreground text-background"
                        : "border-border-default",
                    )}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <label className="mt-5 block text-[10px] text-muted-foreground">
                姿态 · {POSES.length}
              </label>
              <div className="mt-2 grid grid-cols-3 gap-1">
                {POSES.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => setPose(item.id)}
                    className={cn(
                      "rounded-lg border px-1 py-2 text-[9px]",
                      pose === item.id
                        ? "border-foreground bg-foreground text-background"
                        : "border-border-default",
                    )}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <label className="mt-5 block text-[10px] text-muted-foreground">
                位置
              </label>
              <div className="mt-2 grid grid-cols-3 gap-1">
                {(["x", "y", "z"] as const).map((axis) => (
                  <label
                    key={axis}
                    className="flex items-center gap-1 rounded-lg border px-2"
                  >
                    <span className="text-[9px] text-muted-foreground">
                      {axis}
                    </span>
                    <input
                      type="number"
                      step="0.1"
                      value={position[axis]}
                      onChange={(event) =>
                        setPosition((current) => ({
                          ...current,
                          [axis]: Number(event.target.value),
                        }))
                      }
                      className="h-8 min-w-0 flex-1 bg-transparent text-[10px] outline-none"
                    />
                  </label>
                ))}
              </div>
            </>
          ) : selected === "prop" ? (
            (() => {
              const prop = props.find((item) => item.id === selectedPropId);
              if (!prop)
                return (
                  <p className="text-xs text-muted-foreground">
                    道具已从场景移除
                  </p>
                );
              return (
                <>
                  <h3 className="text-sm font-semibold">{prop.name}</h3>
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    场景道具 · {prop.assetId}
                  </p>
                  <div className="mt-5 space-y-2 text-[10px]">
                    <div className="flex items-center rounded-lg border px-3 py-2.5">
                      <span className="text-muted-foreground">形状</span>
                      <span className="ml-auto">{prop.shape}</span>
                    </div>
                    <div className="flex items-center rounded-lg border px-3 py-2.5">
                      <span className="text-muted-foreground">尺寸</span>
                      <span className="ml-auto">
                        {prop.size.map((value) => value.toFixed(2)).join(" × ")}{" "}
                        m
                      </span>
                    </div>
                    <div className="flex items-center rounded-lg border px-3 py-2.5">
                      <span className="text-muted-foreground">材质色</span>
                      <span
                        className="ml-auto size-4 rounded border"
                        style={{ backgroundColor: prop.color }}
                      />
                    </div>
                  </div>
                  <p className="mt-4 rounded-lg bg-muted p-3 text-[9px] leading-4 text-muted-foreground">
                    Agent 可用 set_transform、rename、add_move_path 和 remove
                    继续编排该道具。
                  </p>
                </>
              );
            })()
          ) : (
            (() => {
              const model = models.find((item) => item.id === selectedModelId);
              if (!model)
                return (
                  <p className="text-xs text-muted-foreground">
                    模型已从场景移除
                  </p>
                );
              return (
                <>
                  <h3 className="text-sm font-semibold">{model.name}</h3>
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    Agent 生成的安全声明式模型 · {model.parts.length} 个部件
                  </p>
                  <div className="mt-4 space-y-1.5">
                    {model.parts.map((part) => (
                      <div
                        key={part.id}
                        className="flex items-center gap-2 rounded-lg border border-border-subtle px-2.5 py-2"
                      >
                        <span
                          className="size-3 rounded-sm border"
                          style={{ backgroundColor: part.color }}
                        />
                        <span className="min-w-0 flex-1 truncate text-[10px]">
                          {part.name}
                        </span>
                        <span className="text-[9px] text-muted-foreground">
                          {part.shape}
                        </span>
                      </div>
                    ))}
                  </div>
                  <p className="mt-4 rounded-lg bg-muted p-3 text-[9px] leading-4 text-muted-foreground">
                    使用 model.capture
                    导出多视角图片；结构数据本身不作为视觉验收。
                  </p>
                </>
              );
            })()
          )}
        </aside>
      </div>
    </div>
  );
}
