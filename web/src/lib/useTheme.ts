import { useCallback, useEffect, useState } from "react";

/**
 * Colour scheme follows the OS by default. The explicit modes exist because a
 * demo has to be able to show both on command, not because the system setting is
 * insufficient — "auto" is the default and the one the product ships behaving as.
 */
export type ThemeChoice = "auto" | "light" | "dark";

const KEY = "groundtruth:theme";

function stored(): ThemeChoice {
  try {
    const value = localStorage.getItem(KEY);
    if (value === "light" || value === "dark" || value === "auto") return value;
  } catch {
    // Private mode or blocked storage. Auto is the correct default anyway.
  }
  return "auto";
}

export function useTheme() {
  const [choice, setChoice] = useState<ThemeChoice>(stored);

  useEffect(() => {
    const root = document.documentElement;
    // No attribute means "auto": the token stylesheet's prefers-color-scheme block
    // then applies, which is exactly the OS-following behaviour.
    if (choice === "auto") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", choice);
    try {
      localStorage.setItem(KEY, choice);
    } catch {
      // Not being able to remember the choice is not worth failing a render over.
    }
  }, [choice]);

  const cycle = useCallback(() => {
    setChoice((current) => (current === "auto" ? "light" : current === "light" ? "dark" : "auto"));
  }, []);

  return { choice, setChoice, cycle };
}
