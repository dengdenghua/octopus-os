/**
 * Pluggable workspace modules — types.
 *
 * Modeled on DingTalk's "edit sidebar" panel: every module already ships in
 * the bundle; the catalog only decides which entries a user keeps in their
 * sidebar. Adding one costs no download (pages are `lazy()`, so a hidden
 * module's chunk is simply never fetched).
 *
 * This is deliberately NOT a plugin/app store: nothing here loads remote
 * code. See docs/architecture/blocks.md §2 for that (separate, heavier) path.
 */

/** Business-semantic grouping shown as sections in the editor panel. */
export type ModuleGroup =
  | "workspace" // 工作台核心
  | "knowledge" // 知识与存储
  | "community" // 社区与发现
  | "growth"; // 成长与运营

/** Where a module's entry renders in the sidebar. */
export type ModuleSection =
  | "chatCapability"
  | "community"
  | "storageLibrary";

export interface ModuleDescriptor {
  /** Stable id — the persistence key. Never reuse across modules. */
  id: string;
  /** Route the sidebar entry links to (may carry query params). */
  to: string;
  /** i18n key resolved against the `sidebar` namespace. */
  labelKey: string;
  group: ModuleGroup;
  /** Which sidebar section this entry belongs to. */
  section: ModuleSection;
  /**
   * False = core entry, always visible and not offered in the editor.
   * Guards against a user hiding every way back into the product.
   */
  removable: boolean;
}
