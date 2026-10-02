import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

const STORAGE_KEY = "prdt.judge";

interface JudgeModeContextValue {
  judgeMode: boolean;
  toggle: () => void;
}

const JudgeModeContext = createContext<JudgeModeContextValue | null>(null);

function readInitial(): boolean {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * Presentation ("judge mode") toggle — persists in sessionStorage so a page
 * reload during the demo keeps the enlarged display. FE components opt in via
 * the `judge-enlarge` / `judge-hide` CSS hooks in src/index.css.
 */
export function JudgeModeProvider({ children }: { children: ReactNode }) {
  const [judgeMode, setJudgeMode] = useState<boolean>(readInitial);

  useEffect(() => {
    try {
      window.sessionStorage.setItem(STORAGE_KEY, judgeMode ? "1" : "0");
    } catch {
      // Storage unavailable (private mode etc.) — mode simply won't persist.
    }
  }, [judgeMode]);

  const toggle = useCallback(() => setJudgeMode((value) => !value), []);

  const value = useMemo(() => ({ judgeMode, toggle }), [judgeMode, toggle]);

  return <JudgeModeContext.Provider value={value}>{children}</JudgeModeContext.Provider>;
}

export function useJudgeMode(): JudgeModeContextValue {
  const ctx = useContext(JudgeModeContext);
  if (!ctx) {
    throw new Error("useJudgeMode must be used within a JudgeModeProvider");
  }
  return ctx;
}
