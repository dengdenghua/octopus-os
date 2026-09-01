# Skills × Arms × Agents

> Ground truth for *which agent can invoke which skill* · derived from `_CATALOG` (all_skills) + `make_*_arm` (presets) + 每个 agent 的 `tool-registry.jsonc`。

## Skills catalog

| Skill | Group | Atomic | In arms | Used by agents |
| --- | --- | --- | --- | --- |
| `analyze_soul_impact` | memory | ✅ | — | admin, coder, desktop_operator, general |
| `api-and-interface-design` | agent_docs |  | — | — |
| `append_text_file` | fs_write |  | fs_writer, vibe_selling | admin, coder, general |
| `apply_skill` | skill_library |  | — | — |
| `ask_user_question` | ask_user | ✅ | — | admin, coder, desktop_operator, general |
| `auto_regression_check` | memory | ✅ | — | admin, coder, desktop_operator, general |
| `background_exec` | shell |  | shell | admin, coder, general |
| `bb_keys` | blackboard | ✅ | — | admin, coder, desktop_operator, general |
| `bb_read` | blackboard | ✅ | — | admin, coder, desktop_operator, general |
| `bb_write` | blackboard | ✅ | — | admin, coder, desktop_operator, general |
| `browser-testing-with-devtools` | agent_docs |  | — | — |
| `browser_click` | browser |  | browser_interact | admin, general |
| `browser_extract` | browser |  | browser_read, ecommerce_mind, vibe_selling | admin, general |
| `browser_find` | browser |  | — | — |
| `browser_get` | browser |  | browser_read, ecommerce_mind, vibe_selling | admin, general |
| `browser_navigate` | browser |  | browser_read, ecommerce_mind, vibe_selling | admin, general |
| `browser_screenshot` | browser |  | browser_read, ecommerce_mind, vibe_selling | admin, general |
| `browser_scroll` | browser |  | browser_interact | admin, general |
| `browser_state` | browser |  | — | — |
| `browser_type` | browser |  | browser_interact | admin, general |
| `browser_upload` | browser |  | browser_interact | admin, general |
| `browser_wait` | browser |  | browser_interact | admin, general |
| `call_agent` | delegation |  | — | — |
| `call_agent_background` | jobs |  | — | — |
| `call_agent_parallel` | delegation |  | — | — |
| `cancel_scheduled_task` | cron |  | — | — |
| `code-quality` | agent_docs |  | — | — |
| `code_analyze` | code_intel | ✅ | — | admin, coder, desktop_operator, general |
| `code_dependency_graph` | code_intel | ✅ | — | admin, coder, desktop_operator, general |
| `code_edit_diff` | code_intel |  | — | — |
| `code_find_symbol` | code_intel | ✅ | — | admin, coder, desktop_operator, general |
| `code_search` | code_intel | ✅ | — | admin, coder, desktop_operator, general |
| `computer_execute_token` | computer |  | desktop_operator | admin, desktop_operator, general |
| `computer_observe` | computer |  | desktop_operator | admin, desktop_operator, general |
| `computer_plan_next` | computer |  | desktop_operator | admin, desktop_operator, general |
| `computer_preview_action` | computer |  | desktop_operator | admin, desktop_operator, general |
| `computer_uia_find` | computer |  | desktop_operator | admin, desktop_operator, general |
| `computer_uia_status` | computer |  | desktop_operator | admin, desktop_operator, general |
| `computer_uia_tree` | computer |  | desktop_operator | admin, desktop_operator, general |
| `count_words` | builtin | ✅ | — | admin, coder, desktop_operator, general |
| `crawl_site` | crawler |  | ecommerce_mind, general, vibe_selling, web_read | admin, coder, general |
| `crop_and_replicate_assets_in_image` | kimi_compat |  | — | — |
| `deep_evolve` | memory |  | — | — |
| `deep_reflect` | memory |  | — | — |
| `deploy_website` | kimi_compat |  | — | — |
| `diary_write` | memory | ✅ | — | admin, coder, desktop_operator, general |
| `edit_file` | fs_write |  | — | — |
| `edit_text_file` | fs_write |  | fs_writer, vibe_selling | admin, coder, general |
| `exec_shell` | shell |  | shell | admin, coder, general |
| `execute_skill` | agent_meta | ✅ | — | admin, coder, desktop_operator, general |
| `exit_plan_mode` | mode | ✅ | — | admin, coder, desktop_operator, general |
| `fetch_url` | web |  | ecommerce_mind, general, vibe_selling, web_read | admin, coder, general |
| `file_stats` | builtin | ✅ | — | admin, coder, desktop_operator, general |
| `find_asset_bbox` | kimi_compat |  | — | — |
| `format_code` | code_quality |  | — | — |
| `frontend-design` | agent_docs |  | — | — |
| `frontend-ui-engineering` | agent_docs |  | — | — |
| `generate_image` | kimi_compat |  | — | — |
| `generate_sound_effects` | kimi_compat |  | — | — |
| `generate_speech` | kimi_compat |  | — | — |
| `generate_video` | kimi_compat |  | — | — |
| `get_available_voices` | kimi_compat | ✅ | — | admin, coder, desktop_operator, general |
| `get_data_source` | kimi_compat |  | — | — |
| `get_data_source_desc` | kimi_compat | ✅ | — | admin, coder, desktop_operator, general |
| `git_add` | git |  | git | admin, coder, general |
| `git_branch` | git |  | git | admin, coder, general |
| `git_commit` | git |  | git | admin, coder, general |
| `git_diff` | git |  | git | admin, coder, general |
| `git_log` | git |  | git | admin, coder, general |
| `git_status` | git |  | git | admin, coder, general |
| `glob_files` | fs_search | ✅ | — | admin, coder, desktop_operator, general |
| `grep_text` | fs_search | ✅ | — | admin, coder, desktop_operator, general |
| `hash_text` | builtin | ✅ | — | admin, coder, desktop_operator, general |
| `ipython` | shell |  | — | — |
| `job_kill` | jobs | ✅ | — | admin, coder, desktop_operator, general |
| `job_list` | jobs | ✅ | — | admin, coder, desktop_operator, general |
| `job_output` | jobs |  | — | — |
| `keyboard_press` | computer |  | desktop_operator | admin, desktop_operator, general |
| `keyboard_type` | computer |  | desktop_operator | admin, desktop_operator, general |
| `kg_query` | memory | ✅ | — | admin, coder, desktop_operator, general |
| `kill_background_exec` | shell |  | shell | admin, coder, general |
| `kill_shell` | shell |  | shell | admin, coder, general |
| `learn_skill_from_text` | skill_library |  | — | — |
| `lint_check` | code_quality |  | — | — |
| `list_cwd` | builtin | ✅ | — | admin, coder, desktop_operator, general |
| `list_learned_skills` | skill_library | ✅ | — | admin, coder, desktop_operator, general |
| `list_scheduled_tasks` | cron |  | — | — |
| `list_soul_history` | memory | ✅ | — | admin, coder, desktop_operator, general |
| `live_browser_click` | browser_act |  | — | — |
| `live_browser_current_url` | browser_act |  | — | — |
| `live_browser_execute_js` | browser_act |  | — | — |
| `live_browser_extract` | browser_act |  | — | — |
| `live_browser_find` | browser_act |  | — | — |
| `live_browser_navigate` | browser_act |  | — | — |
| `live_browser_screenshot` | browser_act |  | — | — |
| `live_browser_scroll` | browser_act |  | — | — |
| `live_browser_state` | browser_act |  | — | — |
| `live_browser_type` | browser_act |  | — | — |
| `live_browser_wait` | browser_act |  | — | — |
| `lsp_definition` | lsp |  | — | — |
| `lsp_document_symbols` | lsp |  | — | — |
| `lsp_hover` | lsp |  | — | — |
| `lsp_references` | lsp |  | — | — |
| `mouse_click` | computer |  | desktop_operator | admin, desktop_operator, general |
| `mouse_move` | computer |  | desktop_operator | admin, desktop_operator, general |
| `multi_edit_file` | fs_write |  | — | — |
| `note_user` | memory | ✅ | — | admin, coder, desktop_operator, general |
| `platform_collect` | web |  | ecommerce_mind, general, vibe_selling, web_read | admin, coder, general |
| `platform_monitor` | web |  | ecommerce_mind, general, vibe_selling, web_read | admin, coder, general |
| `platform_read` | web |  | ecommerce_mind, general, vibe_selling, web_read | admin, coder, general |
| `platform_search` | web |  | ecommerce_mind, general, vibe_selling, web_read | admin, coder, general |
| `query_capability` | agent_meta | ✅ | — | admin, coder, desktop_operator, general |
| `query_skill` | agent_meta | ✅ | — | admin, coder, desktop_operator, general |
| `reach_doctor` | web |  | ecommerce_mind, general, vibe_selling, web_read | admin, coder, general |
| `react-best-practices` | agent_docs |  | — | — |
| `read_background_output` | shell |  | shell | admin, coder, general |
| `read_file` | builtin | ✅ | — | admin, coder, desktop_operator, general |
| `read_file_range` | fs_search | ✅ | — | admin, coder, desktop_operator, general |
| `read_shell_output` | shell |  | shell | admin, coder, general |
| `recall` | memory | ✅ | — | admin, coder, desktop_operator, general |
| `recall_scores` | memory | ✅ | — | admin, coder, desktop_operator, general |
| `remember` | memory | ✅ | — | admin, coder, desktop_operator, general |
| `revert_soul` | memory | ✅ | — | admin, coder, desktop_operator, general |
| `run_tests` | code_quality |  | — | — |
| `schedule_task` | cron |  | — | — |
| `screen_capture` | computer |  | desktop_operator | admin, desktop_operator, general |
| `screen_info` | computer |  | desktop_operator | admin, desktop_operator, general |
| `screenshot_web_full_page` | kimi_compat |  | — | — |
| `search_capabilities` | agent_meta | ✅ | — | admin, coder, desktop_operator, general |
| `search_image_by_image` | kimi_compat |  | — | — |
| `search_image_by_text` | kimi_compat |  | — | — |
| `search_skills` | agent_meta | ✅ | — | admin, coder, desktop_operator, general |
| `todo_read` | agent_meta | ✅ | — | admin, coder, desktop_operator, general |
| `todo_write` | agent_meta | ✅ | — | admin, coder, desktop_operator, general |
| `tree` | fs_search | ✅ | — | admin, coder, desktop_operator, general |
| `typescript-best-practices` | agent_docs |  | — | — |
| `update_soul` | memory | ✅ | — | admin, coder, desktop_operator, general |
| `use_capability` | agent_meta | ✅ | — | admin, coder, desktop_operator, general |
| `use_chatgpt_connector` | builtin | ✅ | — | admin, coder, desktop_operator, general |
| `web_fetch` | web |  | ecommerce_mind, general, vibe_selling, web_read | admin, coder, general |
| `web_search` | web |  | ecommerce_mind, general, vibe_selling, web_read | admin, coder, general |
| `website_version_manager` | kimi_compat |  | — | — |
| `workflow` | workflow |  | — | — |
| `write_text_file` | fs_write |  | fs_writer, vibe_selling | admin, coder, general |

