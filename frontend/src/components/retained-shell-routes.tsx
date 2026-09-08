import {
  Activity,
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  Routes,
  useLocation,
  useNavigate,
  type Location,
} from "react-router-dom";
import { workbenchRoute } from "@/core/router/desktop-workspace-route";
import { currentActorId } from "@/core/auth/api";

type Shell = "desktop" | "workbench";
const WorkspaceLocationContext = createContext<Location | null>(null);

/** Preserve both explicit hosts for the current authenticated session. */
export function RetainedShellRoutes({ children }: { children: ReactNode }) {
  const actor = currentActorId();
  const actorRef = useRef(actor);
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  const shell: Shell | null =
    location.pathname === "/desktop"
      ? "desktop"
      : location.pathname.startsWith("/workspace") &&
          params.get("presentation") === "workbench"
        ? "workbench"
        : null;
  const [saved, setSaved] = useState<Partial<Record<Shell, Location>>>({});
  const actorChanged = actorRef.current !== actor;
  useEffect(() => {
    if (!actorChanged) return;
    actorRef.current = actor;
    setSaved({});
  }, [actor, actorChanged]);
  useEffect(() => {
    if (!shell || actorChanged) return;
    setSaved((current) =>
      current[shell] === location ? current : { ...current, [shell]: location },
    );
  }, [actorChanged, location, shell]);
  const next = actorChanged
    ? shell
      ? { [shell]: location }
      : {}
    : shell
      ? { ...saved, [shell]: location }
      : saved;
  return (
    <WorkspaceLocationContext.Provider value={next.workbench ?? null}>
      {["desktop", "workbench"].map((key) => {
        const retainedLocation = next[key as Shell];
        if (!retainedLocation) return null;
        return (
          <Activity
            key={`${actor}:${key}`}
            mode={shell === key ? "visible" : "hidden"}
          >
            <Routes location={retainedLocation}>{children}</Routes>
          </Activity>
        );
      })}
      {!shell && <Routes>{children}</Routes>}
    </WorkspaceLocationContext.Provider>
  );
}

export function ShellModeSwitch() {
  const location = useLocation();
  const previous = useContext(WorkspaceLocationContext);
  const navigate = useNavigate();
  const params = new URLSearchParams(location.search);
  const isWorkbench =
    location.pathname.startsWith("/workspace") &&
    params.get("presentation") === "workbench";
  return (
    <button
      type="button"
      className="rounded-md px-2 py-1 text-xs hover:bg-muted"
      onClick={() => {
        if (isWorkbench) navigate("/desktop");
        else
          navigate(
            workbenchRoute(
              previous
                ? `${previous.pathname}${previous.search}${previous.hash}`
                : "/workspace/realtime/new",
            ),
            { state: previous?.state },
          );
      }}
    >
      {isWorkbench ? "切换到桌面" : "切换到工作台"}
    </button>
  );
}
