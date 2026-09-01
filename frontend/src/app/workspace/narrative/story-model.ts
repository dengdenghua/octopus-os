import type {
  NarrativeChapter,
  NarrativeContextPack,
  NarrativePipelineStage,
  NarrativeReviewRequest,
  NarrativeScene,
} from "./api";

export interface StoryChapterNode extends NarrativeChapter {
  scenes: NarrativeScene[];
}

export interface StoryVolumeNode {
  id: string;
  ordinal: number;
  title: string;
  chapters: StoryChapterNode[];
}

const CHAPTERS_PER_VOLUME = 10;

export const NARRATIVE_PIPELINE_STAGES = [
  { id: "outline", label: "大纲" },
  { id: "draft", label: "初稿" },
  { id: "continuity", label: "连续性" },
  { id: "style", label: "文风" },
  { id: "revision", label: "修订" },
  { id: "editorial", label: "编辑审阅" },
] as const;

export function mergePipelineStages(
  stages: NarrativePipelineStage[],
): NarrativePipelineStage[] {
  const stageMap = new Map(stages.map((stage) => [stage.id, stage]));
  const ordered = NARRATIVE_PIPELINE_STAGES.map(({ id, label }, index) => ({
    id,
    name: label,
    status: stageMap.get(id)?.status || "pending",
    ordinal: stageMap.get(id)?.ordinal || index + 1,
    output: stageMap.get(id)?.output,
    error: stageMap.get(id)?.error,
    actor: stageMap.get(id)?.actor,
    updated_at: stageMap.get(id)?.updated_at,
  })) satisfies NarrativePipelineStage[];
  const knownIds = new Set<string>(
    NARRATIVE_PIPELINE_STAGES.map(({ id }) => id),
  );
  return [...ordered, ...stages.filter((stage) => !knownIds.has(stage.id))];
}

export function contextBudgetUsage(pack: NarrativeContextPack | null): {
  used: number;
  budget: number;
  percentage: number;
  overBudget: boolean;
} {
  if (!pack) return { used: 0, budget: 0, percentage: 0, overBudget: false };
  const budget = Math.max(pack.token_budget, 0);
  const used = Math.max(pack.token_count, 0);
  return {
    used,
    budget,
    percentage: budget ? Math.min(Math.round((used / budget) * 100), 100) : 0,
    overBudget: budget > 0 && used > budget,
  };
}

export function reviewReadiness(review: NarrativeReviewRequest): {
  quorumMet: boolean;
  hasBlockers: boolean;
  canCommit: boolean;
} {
  const quorumMet =
    review.quorum_required > 0 &&
    review.quorum_received >= review.quorum_required;
  const hasBlockers = review.blockers.length > 0;
  return {
    quorumMet,
    hasBlockers,
    canCommit:
      quorumMet &&
      !hasBlockers &&
      !["committed", "rejected", "cancelled"].includes(review.status),
  };
}

export function buildStoryVolumes(
  chapters: NarrativeChapter[],
  scenes: NarrativeScene[],
  branchId: string,
): StoryVolumeNode[] {
  const branchChapters = chapters
    .filter((chapter) => chapter.branch_id === branchId)
    .sort(
      (left, right) =>
        left.ordinal - right.ordinal || left.title.localeCompare(right.title),
    );
  const sceneMap = new Map<string, NarrativeScene[]>();
  for (const scene of scenes) {
    if (scene.branch_id !== branchId) continue;
    const chapterScenes = sceneMap.get(scene.chapter_id) ?? [];
    chapterScenes.push(scene);
    sceneMap.set(scene.chapter_id, chapterScenes);
  }
  for (const chapterScenes of sceneMap.values()) {
    chapterScenes.sort(
      (left, right) =>
        left.ordinal - right.ordinal || left.title.localeCompare(right.title),
    );
  }

  const volumes = new Map<number, StoryChapterNode[]>();
  for (const chapter of branchChapters) {
    const volumeOrdinal =
      Math.floor((Math.max(chapter.ordinal, 1) - 1) / CHAPTERS_PER_VOLUME) + 1;
    const volumeChapters = volumes.get(volumeOrdinal) ?? [];
    volumeChapters.push({
      ...chapter,
      scenes: sceneMap.get(chapter.id) ?? [],
    });
    volumes.set(volumeOrdinal, volumeChapters);
  }

  return [...volumes.entries()]
    .sort(([left], [right]) => left - right)
    .map(([ordinal, volumeChapters]) => ({
      id: `${branchId}:volume:${ordinal}`,
      ordinal,
      title: `第 ${ordinal} 卷`,
      chapters: volumeChapters,
    }));
}

export function nextChapterOrdinal(
  chapters: NarrativeChapter[],
  branchId: string,
): number {
  return (
    chapters.reduce(
      (highest, chapter) =>
        chapter.branch_id === branchId
          ? Math.max(highest, chapter.ordinal)
          : highest,
      0,
    ) + 1
  );
}

export function nextSceneOrdinal(
  scenes: NarrativeScene[],
  chapterId: string,
): number {
  return (
    scenes.reduce(
      (highest, scene) =>
        scene.chapter_id === chapterId
          ? Math.max(highest, scene.ordinal)
          : highest,
      0,
    ) + 1
  );
}
