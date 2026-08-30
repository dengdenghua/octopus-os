import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const pageSource = readFileSync(
  join(process.cwd(), "src/app/workspace/design/page.tsx"),
  "utf8",
);
const routerSource = readFileSync(
  join(process.cwd(), "src/router.tsx"),
  "utf8",
);
const catalogSource = readFileSync(
  join(process.cwd(), "src/core/modules/catalog.ts"),
  "utf8",
);
const designCatalogSource = readFileSync(
  join(process.cwd(), "src/app/workspace/design/design-catalog.ts"),
  "utf8",
);
const directorSource = readFileSync(
  join(process.cwd(), "src/app/workspace/design/director-stage.tsx"),
  "utf8",
);
const comfyEditorSource = readFileSync(
  join(process.cwd(), "src/app/workspace/design/comfy-workflow-editor.tsx"),
  "utf8",
);
describe("Echo Design platform contract", () => {
  it("keeps freeform and workflow in one canvas surface", () => {
    expect(pageSource).toContain('"freeform"');
    expect(pageSource).toContain('"workflow"');
    expect(pageSource).toContain('data-testid="design-infinite-canvas"');
    expect(pageSource).toContain("<EdgeLayer");
    expect(pageSource).toContain("connectionSourceId={connectionSourceId}");
  });

  it("can mount the same project canvas inside a group workbench", () => {
    expect(pageSource).toContain("embeddedProject");
    expect(pageSource).toContain('if (embeddedProject) return "canvas"');
    expect(pageSource).toContain("WORKSPACE_LAYOUT_STORAGE_KEY");
  });

  it("matches the creation-home hierarchy before entering the canvas", () => {
    expect(pageSource).toContain("function DesignHomeView");
    expect(pageSource).toContain("属于你的多模态 Agent 团队");
    expect(pageSource).toContain("描述你要生成的内容");
    expect(pageSource).toContain("Echo 创作基座已就绪");
    expect(pageSource).toContain("<CreativeProjectSelector");
    expect(pageSource).toContain("onSelect={handleCreativeProjectChange}");
    expect(pageSource).toContain("不会与其他角色自动共享");
    expect(pageSource).not.toContain("visibleAgents");
    expect(pageSource).toContain("w-[clamp(380px,32vw,440px)]");
    expect(pageSource).toContain('data-echo-design-chat="true"');
    expect(pageSource).toContain("Design 使用指南");
    expect(pageSource).toContain("模型使用指南");
    expect(pageSource).toContain("新建本地项目");
    expect(pageSource).toContain("当前角色的独立创作房间");
    expect(pageSource).toContain("红人带货");
    expect(pageSource).toContain("使用提示词");
    expect(pageSource).toContain("grid-cols-3");
    expect(pageSource).toContain("createLocalCreativeProject");
    expect(pageSource).toContain('aria-label="添加文件"');
    expect(pageSource).toContain("uploadHomeFiles");
    expect(pageSource).toContain("DESIGN_MODEL_SELECTION_KEY");
    expect(pageSource).toContain("useModels()");
    expect(pageSource).toContain(
      "勾选后，Agent 可在任务中调用这些模型与本地能力",
    );
    expect(pageSource).toContain("可调用创作能力");
    expect(pageSource).toContain(
      'projectId || creativeProjectId ? "canvas" : "home"',
    );
  });

  it("binds real skills and plugins and compiles the graph for AI execution", () => {
    expect(pageSource).toContain("useSkills()");
    expect(pageSource).toContain("installedSkills={skills}");
    expect(pageSource).toContain("useEnableSkill");
    expect(pageSource).toContain("useEnableMarketSkill");
    expect(pageSource).toContain("启用 Skill");
    expect(pageSource).toContain("安装 Skill");
    expect(pageSource).toContain("ensureSkillEnabled");
    expect(pageSource).toContain("handleInstallSkill");
    expect(pageSource).toContain('navigate("/workspace/skills")');
    expect(pageSource).toContain("仅显示未安装");
    expect(pageSource).toContain('value="popular"');
    expect(pageSource).toContain("featuredSkillIds.has(item.id)");
    expect(pageSource).toContain("官方精选");
    expect(pageSource).toContain("用户精选");
    expect(pageSource).toContain("其他 Skill ·");
    expect(pageSource).toContain("/api/design/skills/");
    expect(pageSource).toContain("Skill 文件");
    expect(pageSource).toContain("<MarkdownContent");
    expect(pageSource).toContain("detailDirectories");
    expect(pageSource).toContain("renderedDetailContent");
    expect(pageSource).toContain("\\`\\`\\`yaml");
    expect(pageSource).toContain("Echo 原创 · Apache-2.0");
    expect(pageSource).toContain("<PluginNodeFrame");
    expect(pageSource).toContain("NATIVE_NODE_TEMPLATES.filter");
    expect(pageSource).toContain("useAgents()");
    expect(pageSource).toContain("designCanvasRunPrompt(document)");
    expect(pageSource).toContain('embedded: "design"');
    expect(pageSource).toContain(
      "#/workspace/realtime/new?${params.toString()}",
    );
    expect(pageSource).toContain("setEmbeddedChatUrl");
  });

  it("provides a reusable asset library with MiniMax-style metadata", () => {
    expect(pageSource).toContain("/api/design/assets");
    expect(pageSource).toContain("填写清晰的名称、描述和标签");
    expect(pageSource).toContain("将素材文件拖入 / 点击上传");
    expect(pageSource).toContain("创建资产");
    expect(pageSource).toContain("风格包");
    expect(pageSource).toContain("function CanvasAssetsPanel");
    expect(pageSource).toContain("在画布中定位");
    expect(pageSource).toContain("搜索文件");
    expect(pageSource).toContain("上传文件");
    expect(pageSource).toContain("/api/design/assets/import-pack");
    expect(pageSource).toContain("assetPackInputRef");
    expect(pageSource).toContain("最近添加");
    expect(pageSource).toContain("按名称");
  });

  it("uses the MiniMax-style on-demand node menu and workspace layouts", () => {
    expect(pageSource).toContain("添加节点");
    expect(pageSource).toContain("w-[236px]");
    expect(pageSource).toContain("data-add-node-menu");
    expect(pageSource).toContain("data-add-node-trigger");
    expect(pageSource).toContain("画布 + 个人工作台");
    expect(pageSource).toContain("仅个人工作台");
    expect(pageSource).toContain('title="Echo 个人工作台"');
    expect(pageSource).not.toContain("交给 AI");
    expect(pageSource).toContain("在个人工作台发送需求时");
    expect(pageSource).toContain("<CreativeProjectSelector");
    expect(pageSource).toContain("creation_space");
    expect(pageSource).toContain("仅画布");
    expect(pageSource).toContain("ComfyUI 工作流");
    expect(pageSource).toContain("导入本地工作流");
    expect(pageSource).toContain("在画布中创建");
    expect(pageSource).toContain('aria-label="导入/新建工作流"');
    expect(pageSource).toContain("CANVAS_BACKGROUND_TONES");
    expect(pageSource).toContain("背景样式");
    expect(pageSource).toContain("背景颜色");
    expect(pageSource).toContain("隐藏连线");
    expect(pageSource).toContain("小地图，点击适配全部内容");
    expect(pageSource).toContain("双击画布，自由生成节点");
    expect(pageSource).toContain("onDoubleClick");
    expect(pageSource).toContain("按连线");
    expect(pageSource).toContain("按媒体类型");
    expect(pageSource).toContain("已整理画布");
    expect(pageSource).toContain("恢复");
    expect(pageSource).toContain("switchDesignCanvasMode");
    expect(pageSource).toContain("groupSelection");
    expect(pageSource).toContain("解散分组");
    expect(pageSource).toContain("跟随目标");
    expect(pageSource).toContain("自由贴纸");
    expect(pageSource).toContain("清空全部贴纸");
    expect(pageSource).toContain("添加贴纸");
    expect(pageSource).toContain('aria-label="移动 / 小手工具"');
    expect(pageSource).toContain("小手工具");
    expect(pageSource).toContain('aria-label="帮助指南"');
    expect(pageSource).toContain("功能许愿");
    expect(pageSource).toContain("submitDesignFeedback");
    expect(pageSource).toContain("拖动框选");
    expect(pageSource).toContain("selectionRect");
    expect(pageSource).toContain("开始连接");
    expect(pageSource).toContain("完成连接");
    expect(pageSource).toContain("落到空白处新建并连接");
    expect(pageSource).toContain("data-connection-target");
    expect(pageSource).toContain("elementFromPoint");
    expect(pageSource).toContain("position={");
    expect(pageSource).toContain("addNodePosition.x + NODE_WIDTH / 2");
    expect(pageSource).toContain("节点关系");
    expect(pageSource).toContain("解除连接");
    expect(pageSource).toContain("<span>撤销</span>");
    expect(pageSource).toContain("<span>重做</span>");
    expect(pageSource).toContain("⌘Z / ⇧⌘Z");
    expect(pageSource).toContain("undoHistoryRef");
    expect(pageSource).toContain("复制节点");
    expect(pageSource).toContain("另存为");
    expect(pageSource).toContain("编辑节点标签");
    expect(pageSource).toContain("copyNodeAssetReference");
    expect(pageSource).toContain("downloadNodeAsset");
    expect(pageSource).toContain("在 Finder 中打开");
    expect(pageSource).toContain("添加到对话");
    expect(pageSource).toContain("重命名画布节点");
    expect(pageSource).toContain("存为资产");
    expect(pageSource).toContain("存到项目资产");
    expect(pageSource).toContain("nodeAssetFile");
    expect(pageSource).toContain("已存到项目资产，并绑定当前节点");
    expect(pageSource).toContain("data-media-node");
    expect(pageSource).toContain("<video");
    expect(pageSource).toContain("<audio");
    expect(pageSource).toContain("group/media");
    expect(pageSource).toContain("fitDesignMediaNodeDimensions");
    expect(pageSource).toContain("videoWidth");
    expect(pageSource).toContain("naturalWidth");
    expect(pageSource).toContain("pasteDesignSelection");
    expect(pageSource).toContain('aria-label="画布节点菜单"');
    expect(pageSource).toContain("canvasFileInputRef");
    expect(pageSource).toContain("w-[240px]");
    expect(pageSource).toContain("确认删除大量画布内容");
    expect(pageSource).toContain("planDesignSelectionDeletion");
    expect(pageSource).toContain("data-canvas-context-menu");
    expect(pageSource).toContain('event.code === "Space"');
    expect(pageSource).toContain('side="left"');
    expect(pageSource).toContain("剪辑 Agent");
    expect(pageSource).toContain("导演台 Agent");
    expect(pageSource).toContain("echo.design.close-surface");
    expect(pageSource).not.toContain("关闭 AI 剪辑工坊");
  });

  it("ships a functional embedded 3D director surface", () => {
    expect(pageSource).toContain("<DirectorStage");
    expect(directorSource).toContain("new THREE.WebGLRenderer");
    expect(directorSource).toContain("makeMannequin");
    expect(directorSource).toContain("makeDeclarativeModel");
    expect(directorSource).toContain("程序化模型");
    expect(directorSource).toContain("场景道具");
    expect(directorSource).toContain("makeProp");
    expect(directorSource).toContain("导出图片");
    expect(directorSource).toContain("添加路径");
    expect(directorSource).toContain("场景平移");
    expect(directorSource).toContain("场景旋转");
    expect(directorSource).toContain("背景图片");
    expect(directorSource).toContain("水平旋转");
    expect(directorSource).toContain("球形半径");
    expect(directorSource).toContain("角色标签");
    expect(directorSource).toContain("sceneRootRef");
    expect(directorSource).toContain("timelineZoom");
  });

  it("registers the installable workspace route and module", () => {
    expect(routerSource).toMatch(
      /path="design"\s+element={<RemoteWorkbenchSurface app={DESIGN_APP} \/>}/,
    );
    expect(routerSource).toContain(
      'const DESIGN_APP = remoteWorkbenchApp("design")',
    );
    expect(catalogSource).toContain('id: "design"');
    expect(catalogSource).toContain('to: "/workspace/design"');
  });

  it("isolates local projects and creation rooms by persona", () => {
    expect(pageSource).toContain("useSearchParams()");
    expect(pageSource).toContain("creativeCanvasStorageKey(");
    expect(pageSource).toContain('searchParams.get("creative_project")');
    expect(pageSource).toContain('params.set("creation_space", personaId)');
    expect(pageSource).toContain("useActiveAgentId()");
    expect(pageSource).toContain("readLocalCreativeProjects(personaId)");
    expect(pageSource).not.toContain('searchParams.get("project")');
    expect(pageSource).not.toContain("useProjects()");
    expect(pageSource).not.toContain("<WorkDirSelector");
  });

  it("keeps legacy embedded canvases synchronized inside a group workbench", () => {
    expect(pageSource).toContain(
      "`${DESIGN_CANVAS_STORAGE_KEY}:project:${projectId}`",
    );
    expect(pageSource).toContain(
      "/api/design/projects/${encodeURIComponent(projectId)}/canvas",
    );
    expect(pageSource).toContain(
      "expected_revision: serverRevisionRef.current",
    );
    expect(pageSource).toContain("版本冲突");
    expect(pageSource).toContain("mergeDesignCanvases");
    expect(pageSource).toContain("BroadcastChannel");
    expect(pageSource).toContain("/presence");
    expect(pageSource).toContain("presencePointerRef");
    expect(pageSource).toContain("位成员在线");
    expect(pageSource).toContain("合并保存");
    expect(pageSource).toContain("载入新版");
  });

  it("loads durable project artifacts into the asset center and canvas", () => {
    expect(pageSource).toContain(
      "/api/projects/${encodeURIComponent(projectId)}",
    );
    expect(pageSource).toContain("payload.artifacts ?? []");
    expect(pageSource).toContain('{ type: "asset", id: artifact.id }');
    expect(pageSource).toContain("资产已加入画布");
  });

  it("labels runnable and dependency-gated ComfyUI templates honestly", () => {
    expect(pageSource).toContain('workflow.availability === "bundled"');
    expect(pageSource).toContain("已内置");
    expect(pageSource).toContain("需依赖");
    expect(pageSource).toContain("/api/design/comfyui/queue");
    expect(pageSource).toContain("/api/design/comfyui/dependencies");
    expect(pageSource).toContain("/diagnostics`");
    expect(pageSource).toContain("兼容性诊断");
    expect(pageSource).toContain(
      "已核对节点类型、必填输入、枚举值和本地模型文件",
    );
    expect(pageSource).toContain('controlManagedComfy("install")');
    expect(pageSource).toContain('controlManagedComfy("update")');
    expect(pageSource).toContain('controlManagedComfy("manager/cancel")');
    expect(pageSource).toContain("安装本地引擎");
    expect(pageSource).toContain("不会自动下载模型权重");
    expect(pageSource).toContain("/api/design/comfyui/custom-nodes/registry");
    expect(pageSource).toContain('controlCustomNode("install"');
    expect(pageSource).toContain('controlCustomNode("update"');
    expect(pageSource).toContain('controlCustomNode("uninstall"');
    expect(pageSource).toContain('"rollback"');
    expect(pageSource).toContain("来自官方 Comfy Registry");
    expect(pageSource).toContain("/api/design/comfyui/models");
    expect(pageSource).toContain("Hugging Face / Civitai 公开链接");
    expect(pageSource).toContain('controlModel("download")');
    expect(pageSource).toContain('controlModel("remove"');
    expect(pageSource).toContain('controlModel("restore"');
    expect(pageSource).toContain("/api/design/comfyui/history/");
    expect(pageSource).toContain("直接运行");
    expect(pageSource).toContain("生成完成");
    expect(pageSource).toContain("资源文件");
    expect(pageSource).toContain("selectedWorkflowResources");
    expect(pageSource).toContain("selectedWorkflowNodeTypes");
    expect(pageSource).toContain("个节点");
    expect(pageSource).toContain("来源与许可");
    expect(pageSource).toContain("Echo 原创工作流模板");
  });

  it("exposes the expanded original creative skill collection", () => {
    expect(pageSource).toContain("CREATIVE_SKILL_COLLECTION");
    expect(designCatalogSource).toContain("多模态视频提示词导演");
    expect(designCatalogSource).toContain("数字产品宣传片");
    expect(designCatalogSource).toContain("IP 潮玩六宫格动态海报");
    expect(designCatalogSource).toContain("双人游戏开场");
    expect(designCatalogSource).toContain("纸艺定格科普");
    expect(designCatalogSource).toContain("梦核阈限空间");
    expect(designCatalogSource).toContain("电影风格规则提炼");
  });

  it("previews Director Stage camera, object and character timeline tracks", () => {
    expect(directorSource).toContain("samplePath");
    expect(directorSource).toContain("evaluateTimeline");
    expect(directorSource).toContain('track.type === "camera_path"');
    expect(directorSource).toContain('track.type === "object_path"');
    expect(directorSource).toContain('track.type === "character_animation"');
    expect(directorSource).toContain("motionPoseAt");
    expect(directorSource).toContain('aria-label="时间线播放位置"');
  });

  it("embeds a persistent native Comfy workflow editor", () => {
    expect(pageSource).toContain("<ComfyWorkflowEditor");
    expect(pageSource).toContain("返回 Echo 编辑器");
    expect(comfyEditorSource).toContain("ui: { positions }");
    expect(comfyEditorSource).toContain("expected_revision: revision");
    expect(comfyEditorSource).toContain(
      'window.addEventListener("pointermove"',
    );
    expect(comfyEditorSource).toContain("/api/design/comfyui/queue");
    expect(comfyEditorSource).toContain("/api/design/comfyui/object-info");
    expect(comfyEditorSource).toContain("添加 ComfyUI 节点");
    expect(comfyEditorSource).toContain("版本冲突，请重新打开");
  });
});
