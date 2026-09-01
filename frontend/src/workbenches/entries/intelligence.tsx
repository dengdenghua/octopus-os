import IntelligencePage from "@/app/workspace/intelligence/page";
import { mountStandaloneWorkbench } from "@/workbenches/standalone";

void mountStandaloneWorkbench(
  IntelligencePage,
  "/workspace/intelligence?surface=chat",
);
