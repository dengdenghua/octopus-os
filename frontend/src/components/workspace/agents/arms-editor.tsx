import { useEffect, useMemo, useState } from "react";
import {
  CoinsIcon,
  Loader2Icon,
  SaveIcon,
  SearchIcon,
  UndoIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAgent } from "@/core/agents/hooks";
import {
  useAgentToolRegistry,
  useArms,
  useCapabilityPermissions,
  useSaveAgentToolRegistry,
  useUpdateCapabilityPermission,
} from "@/core/agents/tool-registry-hooks";
import { useI18n } from "@/core/i18n/hooks";
import { useSkills } from "@/core/skills/hooks";
import type { SkillInfo } from "@/core/skills/types";
import { cn } from "@/lib/utils";

interface Props {
  agentId: string;
  initialTab?: "arms" | "skills" | "permissions" | "routing";
}

export function ArmsEditor({ agentId, initialTab = "arms" }: Props) {
  const { t } = useI18n();
  const armsQuery = useArms();
  const registryQuery = useAgentToolRegistry(agentId);
  const save = useSaveAgentToolRegistry(agentId);
  const permissionsQuery = useCapabilityPermissions();
  const updatePermission = useUpdateCapabilityPermission();
  const agentQuery = useAgent(agentId);
  const skillsQuery = useSkills();
  const budget = agentQuery.agent?.budget ?? {};
  const hasBudgetOverride =
    budget.max_tokens != null ||
    budget.max_usd != null ||
    budget.max_iterations != null;

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [selectedPrivateSkills, setSelectedPrivateSkills] = useState<
    Set<string>
  >(new Set());
  const [affinity, setAffinity] = useState("");
  const [skillQuery, setSkillQuery] = useState("");
  const [armFilter, setArmFilter] = useState<"all" | "enabled" | "disabled">(
    "all",
  );
  const [skillFilter, setSkillFilter] = useState<
    "all" | "selected" | "unselected"
  >("all");
  const [skillSourceFilter, setSkillSourceFilter] = useState("all");
  const [tab, setTab] = useState<Props["initialTab"]>(initialTab);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setTab(initialTab);
  }, [initialTab]);

  useEffect(() => {
    if (registryQuery.data) {
      setSelected(new Set(registryQuery.data.arms));
      setSelectedPrivateSkills(new Set(registryQuery.data.private_skills));
      setAffinity(registryQuery.data.extra_affinity.join(", "));
      setDirty(false);
    }
  }, [registryQuery.data]);

  const arms = useMemo(() => armsQuery.data ?? [], [armsQuery.data]);
  const skillCatalog = useMemo(() => {
    const byName = new Map<string, SkillInfo>();
    for (const skill of skillsQuery.skills) {
      if ((skill.kind ?? "domain") !== "domain") continue;
      byName.set(skill.name, skill);
    }
    for (const skillName of registryQuery.data?.private_skills ?? []) {
      if (!byName.has(skillName)) {
        byName.set(skillName, {
          name: skillName,
          description: "",
          enabled: true,
          category: "custom",
          kind: "domain",
        });
      }
    }
    return [...byName.values()].sort((a, b) => a.name.localeCompare(b.name));
  }, [registryQuery.data?.private_skills, skillsQuery.skills]);

  const allSkillNames = useMemo(
    () => skillCatalog.map((skill) => skill.name),
    [skillCatalog],
  );

  const skillSources = useMemo(() => {
    const counts = new Map<
      string,
      { id: string; label: string; count: number }
    >();
    for (const skill of skillCatalog) {
      const id = skill.group || skill.category || "domain";
      const label = skill.category || skill.group || id;
      const current = counts.get(id);
      if (current) current.count += 1;
      else counts.set(id, { id, label, count: 1 });
    }
    return [...counts.values()].sort((a, b) => a.label.localeCompare(b.label));
  }, [skillCatalog]);

  const permissionBySkill = useMemo(() => {
    const next = new Map<
      string,
      { id: string; enabled: boolean; available: boolean }
    >();
    for (const permission of permissionsQuery.data ?? []) {
      for (const skill of permission.skill_names) {
        next.set(skill, {
          id: permission.id,
          enabled: permission.enabled,
          available: permission.available,
        });
      }
    }
    return next;
  }, [permissionsQuery.data]);

  const selectedArmSkills = useMemo(() => {
    const next = new Set<string>();
    for (const arm of arms) {
      if (!selected.has(arm.arm_id)) continue;
      for (const skill of arm.skills) next.add(skill);
    }
    return next;
  }, [arms, selected]);

  const permissionRows = useMemo(() => {
    return (permissionsQuery.data ?? []).map((permission) => {
      const agentSkills = permission.skill_names.filter(
        (skill) =>
          selectedArmSkills.has(skill) || selectedPrivateSkills.has(skill),
      );
      const defaultGranted =
        permission.id === "builtin" || permission.id === "memory";
      const agentGranted = defaultGranted || agentSkills.length > 0;
      return {
        ...permission,
        agentSkills,
        agentGranted,
        defaultGranted,
        effective: permission.enabled && agentGranted,
      };
    });
  }, [permissionsQuery.data, selectedArmSkills, selectedPrivateSkills]);

  const permissionSummary = useMemo(() => {
    const globalEnabled = permissionRows.filter((item) => item.enabled).length;
    const agentGranted = permissionRows.filter(
      (item) => item.agentGranted,
    ).length;
    const effective = permissionRows.filter((item) => item.effective).length;
    return {
      globalEnabled,
      agentGranted,
      effective,
      total: permissionRows.length,
    };
  }, [permissionRows]);

  const visibleArms = useMemo(() => {
    if (armFilter === "enabled") {
      return arms.filter((arm) => selected.has(arm.arm_id));
    }
    if (armFilter === "disabled") {
      return arms.filter((arm) => !selected.has(arm.arm_id));
    }
    return arms;
  }, [armFilter, arms, selected]);

  const visibleSkills = useMemo(() => {
    const query = skillQuery.trim().toLowerCase();
    return skillCatalog.filter((skill) => {
      if (
        skillFilter === "selected" &&
        !selectedPrivateSkills.has(skill.name)
      ) {
        return false;
      }
      if (
        skillFilter === "unselected" &&
        selectedPrivateSkills.has(skill.name)
      ) {
        return false;
      }
      if (skillSourceFilter !== "all") {
        if (skillSourceFilter === "custom") {
          if (
            skill.group ||
            skill.trusted_source?.startsWith("skill://all_skills/")
          ) {
            return false;
          }
        } else if (
          (skill.group || skill.category || "domain") !== skillSourceFilter
        ) {
          return false;
        }
      }
      if (!query) return true;
      return [skill.name, skill.description, skill.category, skill.group]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(query);
    });
  }, [
    selectedPrivateSkills,
    skillCatalog,
    skillFilter,
    skillQuery,
    skillSourceFilter,
  ]);

  const original = useMemo(
    () => ({
      arms: new Set(registryQuery.data?.arms ?? []),
      privateSkills: new Set(registryQuery.data?.private_skills ?? []),
      affinity: (registryQuery.data?.extra_affinity ?? []).join(", "),
    }),
    [registryQuery.data],
  );

  const isDirty =
    dirty ||
    selected.size !== original.arms.size ||
    selectedPrivateSkills.size !== original.privateSkills.size ||
    [...selected].some((x) => !original.arms.has(x)) ||
    [...selectedPrivateSkills].some((x) => !original.privateSkills.has(x)) ||
    affinity.trim() !== original.affinity.trim();

  function toggle(armId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(armId)) next.delete(armId);
      else next.add(armId);
      return next;
    });
    setDirty(true);
  }

  function togglePrivateSkill(skill: string) {
    setSelectedPrivateSkills((prev) => {
      const next = new Set(prev);
      if (next.has(skill)) next.delete(skill);
      else next.add(skill);
      return next;
    });
    setDirty(true);
  }

  function onReset() {
    if (!registryQuery.data) return;
    setSelected(new Set(registryQuery.data.arms));
    setSelectedPrivateSkills(new Set(registryQuery.data.private_skills));
    setAffinity(registryQuery.data.extra_affinity.join(", "));
    setSkillQuery("");
    setArmFilter("all");
    setSkillFilter("all");
    setSkillSourceFilter("all");
    setDirty(false);
  }

  function enableVisibleSkills() {
    if (visibleSkills.length === 0) return;
    setSelectedPrivateSkills((prev) => {
      const next = new Set(prev);
      for (const skill of visibleSkills) next.add(skill.name);
      return next;
    });
    setDirty(true);
  }

  function enableAllSkills() {
    if (allSkillNames.length === 0) return;
    setSelectedPrivateSkills(new Set(allSkillNames));
    setDirty(true);
  }

  function disableAllSkills() {
    if (allSkillNames.length === 0) return;
    setSelectedPrivateSkills(new Set());
    setDirty(true);
  }

  function disableVisibleSkills() {
    if (visibleSkills.length === 0) return;
    setSelectedPrivateSkills((prev) => {
      const next = new Set(prev);
      for (const skill of visibleSkills) next.delete(skill.name);
      return next;
    });
    setDirty(true);
  }

  async function onSave() {
    if (!registryQuery.data) return;
    const orderedArms = arms
      .map((a) => a.arm_id)
      .filter((id) => selected.has(id));
    const extra = affinity
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    try {
      await save.mutateAsync({
        arms: orderedArms,
        extra_affinity: extra,
        private_skills: [...selectedPrivateSkills].sort((a, b) =>
          a.localeCompare(b),
        ),
      });
      toast.success(t.armsEditor.saved(agentId));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(t.armsEditor.saveFailed(msg));
    }
  }

  async function togglePermission(group: string, enabled: boolean) {
    try {
      await updatePermission.mutateAsync({ group, enabled });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(t.armsEditor.permissionUpdateFailed(msg));
    }
  }

  if (armsQuery.isLoading || registryQuery.isLoading || skillsQuery.isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-sm text-muted-foreground">
        <Loader2Icon className="mr-2 h-4 w-4 animate-spin" />
        {t.armsEditor.loading}
      </div>
    );
  }

  if (armsQuery.error || registryQuery.error || skillsQuery.error) {
    const err = armsQuery.error ?? registryQuery.error ?? skillsQuery.error;
    return (
      <div className="py-6 text-sm text-destructive">
        {t.armsEditor.loadFailed(
          err instanceof Error ? err.message : String(err),
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4 pb-1">
      <div className="rounded-sm border border-border bg-card/80 px-3 py-2 text-sm text-muted-foreground">
        {t.armsEditor.description}
      </div>

      <Tabs value={tab} onValueChange={(value) => setTab(value as typeof tab)}>
        <TabsList className="grid w-full grid-cols-4 rounded-sm border border-border bg-card/70 p-1">
          <TabsTrigger
            value="arms"
            className="rounded-sm data-[state=active]:bg-primary data-[state=active]:text-primary-foreground"
          >
            {t.armsEditor.armsTab}
          </TabsTrigger>
          <TabsTrigger
            value="skills"
            className="rounded-sm data-[state=active]:bg-primary data-[state=active]:text-primary-foreground"
          >
            {t.armsEditor.skillsTab}
          </TabsTrigger>
          <TabsTrigger
            value="permissions"
            className="rounded-sm data-[state=active]:bg-primary data-[state=active]:text-primary-foreground"
          >
            {t.armsEditor.permissionsTab}
          </TabsTrigger>
          <TabsTrigger
            value="routing"
            className="rounded-sm data-[state=active]:bg-primary data-[state=active]:text-primary-foreground"
          >
            {t.armsEditor.routingTab}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="arms" className="mt-2 space-y-3">
          <div className="flex items-center justify-between gap-3">
            <div className="text-sm font-medium">
              {t.armsEditor.availableArmsLabel}
            </div>
            <Badge variant="outline" className="text-xs">
              {t.armsEditor.selectedArmsCount(selected.size, arms.length)}
            </Badge>
          </div>
          <div className="flex flex-wrap gap-2">
            {[
              ["all", t.armsEditor.filterAll],
              ["enabled", t.armsEditor.filterEnabled],
              ["disabled", t.armsEditor.filterDisabled],
            ].map(([value, label]) => (
              <Button
                key={value}
                size="sm"
                variant={armFilter === value ? "default" : "outline"}
                className="h-8 rounded-sm"
                onClick={() => setArmFilter(value as typeof armFilter)}
              >
                {label}
              </Button>
            ))}
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            {visibleArms.map((arm) => {
              const isOn = selected.has(arm.arm_id);
              return (
                <div
                  key={arm.arm_id}
                  className={cn(
                    "relative overflow-hidden rounded-sm border p-3 transition-colors",
                    "before:pointer-events-none before:absolute before:left-0 before:top-0 before:h-2 before:w-2 before:border-l before:border-t",
                    isOn
                      ? "border-primary/45 bg-primary/5 before:border-primary/70"
                      : "border-border bg-card/55 before:border-border",
                  )}
                >
                  <div className="flex items-start gap-3">
                    <Switch
                      checked={isOn}
                      onCheckedChange={() => toggle(arm.arm_id)}
                      className="mt-1 shrink-0"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        {arm.icon ? (
                          <span className="text-base leading-none">
                            {arm.icon}
                          </span>
                        ) : null}
                        <div className="font-medium">
                          {arm.display_name || arm.arm_id}
                        </div>
                        <Badge variant="outline" className="text-xs">
                          {arm.arm_id}
                        </Badge>
                      </div>
                      {arm.description ? (
                        <div className="mt-1 text-xs text-muted-foreground">
                          {arm.description}
                        </div>
                      ) : null}
                      {arm.skills.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1">
                          {arm.skills.map((s) => (
                            <Badge
                              key={s}
                              variant="outline"
                              className="rounded-sm bg-background/60 text-xs font-mono"
                            >
                              {s}
                            </Badge>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
            {visibleArms.length === 0 ? (
              <div className="rounded-sm border border-dashed border-border bg-card/50 p-6 text-center text-sm text-muted-foreground lg:col-span-2">
                {t.armsEditor.noArmsFound}
              </div>
            ) : null}
          </div>
        </TabsContent>

        <TabsContent value="skills" className="mt-2 space-y-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 text-sm font-medium">
                {t.armsEditor.privateSkillsLabel}
                <Badge variant="secondary" className="rounded-sm text-xs">
                  {t.armsEditor.skillMarketplaceLabel}
                </Badge>
              </div>
              <div className="mt-0.5 text-xs text-muted-foreground">
                {t.armsEditor.privateSkillsHint}
              </div>
            </div>
            <Badge variant="outline" className="text-xs">
              {t.armsEditor.selectedSkillsCount(selectedPrivateSkills.size)}
            </Badge>
          </div>

          <div className="relative">
            <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={skillQuery}
              onChange={(event) => setSkillQuery(event.target.value)}
              placeholder={t.armsEditor.skillSearchPlaceholder}
              className="h-9 rounded-sm border-border bg-card/70 pl-8 text-sm"
            />
          </div>
          <div className="flex flex-wrap gap-2">
            {[
              ["all", t.armsEditor.filterAll],
              ["selected", t.armsEditor.filterSelected],
              ["unselected", t.armsEditor.filterUnselected],
            ].map(([value, label]) => (
              <Button
                key={value}
                size="sm"
                variant={skillFilter === value ? "default" : "outline"}
                className="h-8 rounded-sm"
                onClick={() => setSkillFilter(value as typeof skillFilter)}
              >
                {label}
              </Button>
            ))}
          </div>

          <div className="space-y-2 rounded-sm border border-border bg-card/55 p-2.5">
            <div className="flex items-center justify-between gap-3">
              <div className="font-mono text-xs uppercase tracking-caps text-muted-foreground">
                {t.armsEditor.skillMarketplaceLabel}
              </div>
              <div className="font-mono text-xs text-muted-foreground">
                {t.armsEditor.visibleSkillsCount(
                  visibleSkills.length,
                  skillCatalog.length,
                )}
              </div>
            </div>
            <div className="flex max-h-20 flex-wrap gap-1.5 overflow-y-auto pr-1">
              {[
                ["all", t.armsEditor.skillCategoryAll, skillCatalog.length],
                ...skillSources.map(
                  (source) => [source.id, source.label, source.count] as const,
                ),
                [
                  "custom",
                  t.armsEditor.skillCategoryCustom,
                  skillCatalog.filter(
                    (skill) =>
                      !skill.group &&
                      !skill.trusted_source?.startsWith("skill://all_skills/"),
                  ).length,
                ] as const,
              ].map(([id, label, count]) => (
                <Button
                  key={id}
                  className="h-7 max-w-[180px] justify-between rounded-sm px-2 text-xs"
                  size="sm"
                  variant={skillSourceFilter === id ? "default" : "ghost"}
                  onClick={() => setSkillSourceFilter(String(id))}
                >
                  <span className="min-w-0 truncate">{label}</span>
                  <span className="ml-2 font-mono text-xs opacity-75">
                    {count}
                  </span>
                </Button>
              ))}
            </div>
            <div className="grid gap-2 lg:grid-cols-[1fr_auto]">
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-sm border border-border bg-background/70 px-2.5 py-1.5">
                  <div className="font-mono text-xs uppercase tracking-caps text-muted-foreground">
                    {t.armsEditor.filterAll}
                  </div>
                  <div className="text-sm font-semibold">
                    {skillCatalog.length}
                  </div>
                </div>
                <div className="rounded-sm border border-border bg-background/70 px-2.5 py-1.5">
                  <div className="font-mono text-xs uppercase tracking-caps text-muted-foreground">
                    {t.armsEditor.filterSelected}
                  </div>
                  <div className="text-sm font-semibold">
                    {selectedPrivateSkills.size}
                  </div>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2 lg:justify-end">
                <Button
                  className="h-7 rounded-sm"
                  disabled={skillCatalog.length === 0}
                  size="sm"
                  onClick={enableAllSkills}
                >
                  {t.armsEditor.enableAllSkills}
                </Button>
                <Button
                  className="h-7 rounded-sm"
                  disabled={skillCatalog.length === 0}
                  size="sm"
                  variant="outline"
                  onClick={disableAllSkills}
                >
                  {t.armsEditor.disableAllSkills}
                </Button>
                <Button
                  className="h-7 rounded-sm"
                  disabled={visibleSkills.length === 0}
                  size="sm"
                  onClick={enableVisibleSkills}
                >
                  {t.armsEditor.enableVisibleSkills}
                </Button>
                <Button
                  className="h-7 rounded-sm"
                  disabled={visibleSkills.length === 0}
                  size="sm"
                  variant="outline"
                  onClick={disableVisibleSkills}
                >
                  {t.armsEditor.disableVisibleSkills}
                </Button>
              </div>
            </div>
          </div>

          <div className="grid max-h-[470px] gap-2 overflow-y-auto pr-1 lg:grid-cols-2">
            {visibleSkills.length ? (
              visibleSkills.map((skill) => {
                const isOn = selectedPrivateSkills.has(skill.name);
                const permission = permissionBySkill.get(skill.name);
                const source =
                  skill.group || skill.category || skill.trusted_source || "";
                const skillLabel =
                  skill.name === "*"
                    ? t.agentConfig.allSkillsWildcard
                    : skill.name;
                return (
                  <div
                    key={skill.name}
                    className={cn(
                      "relative overflow-hidden rounded-sm border p-3 transition-colors",
                      "before:pointer-events-none before:absolute before:left-0 before:top-0 before:h-2 before:w-2 before:border-l before:border-t",
                      isOn
                        ? "border-primary/45 bg-primary/5 before:border-primary/70"
                        : "border-border bg-card/55 before:border-border",
                    )}
                  >
                    <div className="flex items-start gap-3">
                      <Switch
                        checked={isOn}
                        onCheckedChange={() => togglePrivateSkill(skill.name)}
                        className="mt-1 shrink-0"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex min-w-0 flex-wrap items-center gap-2">
                          <div className="min-w-0 truncate font-mono text-xs font-medium">
                            {skillLabel}
                          </div>
                          <Badge
                            variant={skill.enabled ? "outline" : "secondary"}
                            className="rounded-sm text-xs"
                          >
                            {skill.enabled
                              ? t.armsEditor.permissionEnabled
                              : t.armsEditor.permissionDisabled}
                          </Badge>
                          {permission ? (
                            <Badge
                              variant={
                                permission.enabled ? "outline" : "secondary"
                              }
                              className={cn(
                                "rounded-sm text-xs",
                                !permission.enabled &&
                                  "border-destructive/30 text-destructive",
                              )}
                            >
                              {permission.id} ·{" "}
                              {permission.enabled
                                ? t.armsEditor.permissionEnabled
                                : t.armsEditor.permissionDisabled}
                            </Badge>
                          ) : null}
                        </div>
                        <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                          {skill.description ||
                            (source
                              ? t.armsEditor.skillSource(source)
                              : t.armsEditor.customSkillSource)}
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })
            ) : (
              <div className="rounded-sm border border-dashed border-border bg-card/50 p-6 text-center text-sm text-muted-foreground lg:col-span-2">
                {t.armsEditor.noSkillsFound}
              </div>
            )}
          </div>
        </TabsContent>

        <TabsContent value="permissions" className="mt-2 space-y-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-medium">
                {t.armsEditor.permissionsLabel}
              </div>
              <div className="mt-0.5 text-xs text-muted-foreground">
                {t.armsEditor.permissionsHint}
              </div>
            </div>
            <Badge variant="outline" className="text-xs">
              {t.armsEditor.permissionEffectiveCount(
                permissionSummary.effective,
                permissionSummary.total,
              )}
            </Badge>
          </div>

          <div className="grid gap-2 md:grid-cols-3">
            <div className="rounded-sm border border-border bg-card/60 px-3 py-2">
              <div className="font-mono text-xs uppercase tracking-caps text-muted-foreground">
                {t.armsEditor.permissionGlobalGate}
              </div>
              <div className="mt-1 text-sm font-semibold">
                {permissionSummary.globalEnabled}/{permissionSummary.total}
              </div>
            </div>
            <div className="rounded-sm border border-border bg-card/60 px-3 py-2">
              <div className="font-mono text-xs uppercase tracking-caps text-muted-foreground">
                {t.armsEditor.permissionAgentGrant}
              </div>
              <div className="mt-1 text-sm font-semibold">
                {permissionSummary.agentGranted}/{permissionSummary.total}
              </div>
            </div>
            <div className="rounded-sm border border-border bg-card/60 px-3 py-2">
              <div className="font-mono text-xs uppercase tracking-caps text-muted-foreground">
                {t.armsEditor.permissionEffective}
              </div>
              <div className="mt-1 text-sm font-semibold">
                {permissionSummary.effective}/{permissionSummary.total}
              </div>
            </div>
          </div>

          {permissionsQuery.isLoading ? (
            <div className="flex items-center justify-center rounded-sm border border-border bg-card/50 p-8 text-sm text-muted-foreground">
              <Loader2Icon className="mr-2 h-4 w-4 animate-spin" />
              {t.armsEditor.loading}
            </div>
          ) : permissionsQuery.error ? (
            <div className="rounded-sm border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
              {t.armsEditor.loadFailed(
                permissionsQuery.error instanceof Error
                  ? permissionsQuery.error.message
                  : String(permissionsQuery.error),
              )}
            </div>
          ) : (
            <div className="grid gap-3 lg:grid-cols-2">
              {permissionRows.map((permission) => {
                const shownAgentSkills = permission.agentSkills.slice(0, 8);
                const shownPermissionSkills = permission.skill_names.slice(
                  0,
                  10,
                );
                return (
                  <div
                    key={permission.id}
                    className={cn(
                      "relative overflow-hidden rounded-sm border p-3 transition-colors",
                      "before:pointer-events-none before:absolute before:left-0 before:top-0 before:h-2 before:w-2 before:border-l before:border-t",
                      permission.effective
                        ? "border-primary/45 bg-primary/5 before:border-primary/70"
                        : "border-border bg-card/55 before:border-border",
                    )}
                  >
                    <div className="flex items-start gap-3">
                      <Switch
                        checked={permission.enabled}
                        disabled={updatePermission.isPending}
                        onCheckedChange={(enabled) =>
                          void togglePermission(permission.id, enabled)
                        }
                        className="mt-1 shrink-0"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <div className="font-mono text-xs font-semibold uppercase tracking-caps">
                            {permission.id}
                          </div>
                          <Badge
                            variant={
                              permission.available ? "outline" : "secondary"
                            }
                            className="rounded-sm text-xs"
                          >
                            {permission.available
                              ? t.armsEditor.permissionAvailable
                              : t.armsEditor.permissionUnavailable}
                          </Badge>
                          <Badge
                            variant={
                              permission.enabled ? "outline" : "secondary"
                            }
                            className={cn(
                              "rounded-sm text-xs",
                              !permission.enabled &&
                                "border-destructive/30 text-destructive",
                            )}
                          >
                            {t.armsEditor.permissionGlobalGate}:{" "}
                            {permission.enabled
                              ? t.armsEditor.permissionEnabled
                              : t.armsEditor.permissionDisabled}
                          </Badge>
                          <Badge
                            variant={
                              permission.agentGranted
                                ? "outline"
                                : "secondary"
                            }
                            className={cn(
                              "rounded-sm text-xs",
                              !permission.agentGranted &&
                                "border-warning/30 text-warning",
                            )}
                          >
                            {permission.defaultGranted
                              ? t.armsEditor.permissionAgentDefault
                              : permission.agentGranted
                                ? t.armsEditor.permissionAgentGranted
                                : t.armsEditor.permissionAgentDenied}
                          </Badge>
                        </div>
                        <div className="mt-2 text-xs text-muted-foreground">
                          {permission.effective
                            ? t.armsEditor.permissionEffectiveHint
                            : !permission.enabled
                              ? t.armsEditor.permissionBlockedByGlobal
                              : t.armsEditor.permissionBlockedByAgent}
                        </div>
                        {shownAgentSkills.length > 0 ? (
                          <div className="mt-2 flex flex-wrap gap-1">
                            {shownAgentSkills.map((skill) => (
                              <Badge
                                key={skill}
                                variant="outline"
                                className="rounded-sm bg-background/70 text-xs font-mono"
                              >
                                {skill}
                              </Badge>
                            ))}
                            {permission.agentSkills.length >
                            shownAgentSkills.length ? (
                              <Badge
                                variant="secondary"
                                className="rounded-sm text-xs"
                              >
                                +
                                {permission.agentSkills.length -
                                  shownAgentSkills.length}
                              </Badge>
                            ) : null}
                          </div>
                        ) : permission.defaultGranted ? (
                          <div className="mt-2 text-xs text-muted-foreground">
                            {t.armsEditor.permissionDefaultGrantHint}
                          </div>
                        ) : null}
                        {permission.skill_names.length > 0 ? (
                          <div className="mt-2 flex max-h-16 flex-wrap gap-1 overflow-hidden border-t border-border-default pt-2">
                            {shownPermissionSkills.map((skill) => (
                              <Badge
                                key={skill}
                                variant="outline"
                                className="rounded-sm bg-background/40 text-xs font-mono text-muted-foreground"
                              >
                                {skill}
                              </Badge>
                            ))}
                            {permission.skill_names.length >
                            shownPermissionSkills.length ? (
                              <Badge
                                variant="secondary"
                                className="rounded-sm text-xs"
                              >
                                +
                                {permission.skill_names.length -
                                  shownPermissionSkills.length}
                              </Badge>
                            ) : null}
                          </div>
                        ) : null}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </TabsContent>

        <TabsContent value="routing" className="mt-2 space-y-4">
          <div>
            <label className="text-sm font-medium">
              {t.armsEditor.extraAffinityLabel}
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                {t.armsEditor.extraAffinityHint}
              </span>
            </label>
            <input
              type="text"
              value={affinity}
              onChange={(e) => {
                setAffinity(e.target.value);
                setDirty(true);
              }}
              placeholder={t.armsEditor.extraAffinityPlaceholder}
              className="mt-1 w-full rounded-sm border border-border bg-card/70 px-3 py-2 text-sm shadow-[var(--shadow-xs)] focus:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            />
          </div>

          <div className="rounded-sm border border-border bg-card/70 p-3">
            <div className="flex items-center gap-2 text-sm font-medium">
              <CoinsIcon className="h-4 w-4 text-muted-foreground" />
              {t.armsEditor.budgetLabel}
              {hasBudgetOverride ? (
                <Badge variant="outline" className="text-xs">
                  {t.armsEditor.budgetOverride}
                </Badge>
              ) : (
                <Badge
                  variant="outline"
                  className="text-xs text-muted-foreground"
                >
                  {t.armsEditor.budgetDefault}
                </Badge>
              )}
            </div>
            <div className="mt-2 grid grid-cols-3 gap-3 text-xs">
              <div>
                <div className="text-muted-foreground">max_iterations</div>
                <div className="mt-0.5 font-mono">
                  {budget.max_iterations ?? "-"}
                </div>
              </div>
              <div>
                <div className="text-muted-foreground">max_tokens</div>
                <div className="mt-0.5 font-mono">
                  {budget.max_tokens != null
                    ? `${(budget.max_tokens / 1000).toFixed(0)}k`
                    : "-"}
                </div>
              </div>
              <div>
                <div className="text-muted-foreground">max_usd</div>
                <div className="mt-0.5 font-mono">
                  {budget.max_usd != null
                    ? `$${budget.max_usd.toFixed(2)}`
                    : "-"}
                </div>
              </div>
            </div>
            {!hasBudgetOverride && (
              <div className="mt-2 text-xs text-muted-foreground">
                {t.armsEditor.budgetEditHint(agentId)}
              </div>
            )}
          </div>
        </TabsContent>
      </Tabs>

      <Separator className="bg-border" />

      <div className="sticky bottom-0 -mx-5 flex items-center justify-end gap-2 border-t border-border bg-background/95 px-5 py-3 backdrop-blur">
        <Button
          variant="outline"
          size="sm"
          onClick={onReset}
          disabled={!isDirty || save.isPending}
          className="rounded-sm"
        >
          <UndoIcon className="mr-1 h-3.5 w-3.5" />
          {t.armsEditor.reset}
        </Button>
        <Button
          size="sm"
          onClick={onSave}
          disabled={!isDirty || save.isPending}
          className="rounded-sm"
        >
          {save.isPending ? (
            <Loader2Icon className="mr-1 h-3.5 w-3.5 animate-spin" />
          ) : (
            <SaveIcon className="mr-1 h-3.5 w-3.5" />
          )}
          {t.armsEditor.saveAndReload}
        </Button>
      </div>
    </div>
  );
}
