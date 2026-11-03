import type { ReactNode } from "react";
import { ReplayContext } from "./context";
import { useReplay } from "./useReplay";

interface Props {
  children: ReactNode;
}

export function ReplayProvider({ children }: Props) {
  const value = useReplay();
  return <ReplayContext.Provider value={value}>{children}</ReplayContext.Provider>;
}
