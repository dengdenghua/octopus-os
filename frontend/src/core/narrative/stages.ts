/**
 * The canonical Narrative Studio pipeline. Keep this mapping small and
 * explicit: a caller may choose the current stage, but it may not choose an
 * arbitrary subagent role.
 */
export const NARRATIVE_STAGE_ORDER = [
  "outline",
  "draft",
  "continuity",
  "style",
  "revision",
  "editorial",
] as const;

export type NarrativeStageName = (typeof NARRATIVE_STAGE_ORDER)[number];

export type NarrativeSubagentType =
  | "narrative-outline"
  | "narrative-draft"
  | "narrative-continuity"
  | "narrative-style"
  | "narrative-revision"
  | "narrative-editorial";

export interface NarrativeStageDefinition {
  readonly name: NarrativeStageName;
  readonly ordinal: number;
  readonly subagentType: NarrativeSubagentType;
  readonly instruction: string;
}

const definitions = [
  {
    name: "outline",
    ordinal: 1,
    subagentType: "narrative-outline",
    instruction:
      "Create a scene-level candidate outline with dramatic objective, POV, beats, turning point, emotional change, continuity dependencies, and foreshadowing.",
  },
  {
    name: "draft",
    ordinal: 2,
    subagentType: "narrative-draft",
    instruction:
      "Write candidate prose from the supplied outline and cited facts. Mark unsupported necessities as [NEEDS DECISION].",
  },
  {
    name: "continuity",
    ordinal: 3,
    subagentType: "narrative-continuity",
    instruction:
      "Audit the candidate draft for continuity, chronology, knowledge, motivation, location, inventory, state, and foreshadowing conflicts.",
  },
  {
    name: "style",
    ordinal: 4,
    subagentType: "narrative-style",
    instruction:
      "Critique voice, pacing, clarity, dialogue, repetition, exposition, tone, and cliches without changing story facts.",
  },
  {
    name: "revision",
    ordinal: 5,
    subagentType: "narrative-revision",
    instruction:
      "Produce revised candidate prose that addresses the supplied continuity and style findings, followed by a concise change log.",
  },
  {
    name: "editorial",
    ordinal: 6,
    subagentType: "narrative-editorial",
    instruction:
      "Score the revised candidate and recommend approve, revise, or block for later human governance. Approval is not a canon commit.",
  },
] as const satisfies readonly NarrativeStageDefinition[];

export const NARRATIVE_STAGE_DEFINITIONS: readonly NarrativeStageDefinition[] =
  Object.freeze(
    definitions.map((definition) => Object.freeze({ ...definition })),
  );

export const NARRATIVE_STAGE_AGENT_MAP: Readonly<
  Record<NarrativeStageName, NarrativeSubagentType>
> = Object.freeze(
  Object.fromEntries(
    NARRATIVE_STAGE_DEFINITIONS.map(({ name, subagentType }) => [
      name,
      subagentType,
    ]),
  ) as Record<NarrativeStageName, NarrativeSubagentType>,
);

const DEFINITION_BY_NAME = new Map(
  NARRATIVE_STAGE_DEFINITIONS.map((definition) => [
    definition.name,
    definition,
  ]),
);

export function isNarrativeStageName(
  value: unknown,
): value is NarrativeStageName {
  return (
    typeof value === "string" &&
    DEFINITION_BY_NAME.has(value as NarrativeStageName)
  );
}

export function getNarrativeStageDefinition(
  stage: NarrativeStageName,
): NarrativeStageDefinition {
  const definition = DEFINITION_BY_NAME.get(stage);
  if (!definition) {
    throw new Error(`Unsupported narrative pipeline stage: ${String(stage)}`);
  }
  return definition;
}

export function getNarrativeSubagentForStage(
  stage: NarrativeStageName,
): NarrativeSubagentType {
  return getNarrativeStageDefinition(stage).subagentType;
}
