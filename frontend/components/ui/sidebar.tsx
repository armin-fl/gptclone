"use client";

import * as React from "react";
import { PanelLeft } from "lucide-react";

import { cn } from "@/lib/utils";

interface SidebarContextValue {
  open: boolean;
  setOpen: (open: boolean) => void;
  toggleSidebar: () => void;
}

const SidebarContext = React.createContext<SidebarContextValue | null>(null);

function useSidebar() {
  const context = React.useContext(SidebarContext);
  if (!context) {
    throw new Error("useSidebar must be used within a SidebarProvider.");
  }
  return context;
}

interface SidebarProviderProps extends React.ComponentProps<"div"> {
  defaultOpen?: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

function SidebarProvider({
  defaultOpen = true,
  open: controlledOpen,
  onOpenChange,
  className,
  children,
  ...props
}: SidebarProviderProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = React.useState(defaultOpen);
  const open = controlledOpen ?? uncontrolledOpen;

  const setOpen = React.useCallback(
    (nextOpen: boolean) => {
      if (controlledOpen === undefined) {
        setUncontrolledOpen(nextOpen);
      }
      onOpenChange?.(nextOpen);
    },
    [controlledOpen, onOpenChange],
  );

  const value = React.useMemo<SidebarContextValue>(
    () => ({
      open,
      setOpen,
      toggleSidebar: () => setOpen(!open),
    }),
    [open, setOpen],
  );

  return (
    <SidebarContext.Provider value={value}>
      <div
        data-sidebar-wrapper
        data-state={open ? "open" : "closed"}
        className={cn("group/sidebar-wrapper flex min-h-screen w-full", className)}
        {...props}
      >
        {children}
      </div>
    </SidebarContext.Provider>
  );
}

const SidebarInset = React.forwardRef<HTMLElement, React.ComponentProps<"main">>(
  ({ className, ...props }, ref) => (
    <main ref={ref} className={cn("flex min-w-0 flex-1 flex-col", className)} {...props} />
  ),
);
SidebarInset.displayName = "SidebarInset";

const SidebarTrigger = React.forwardRef<HTMLButtonElement, React.ComponentProps<"button">>(
  ({ className, onClick, children, type = "button", ...props }, ref) => {
    const { toggleSidebar } = useSidebar();

    return (
      <button
        ref={ref}
        type={type}
        onClick={(event) => {
          onClick?.(event);
          if (!event.defaultPrevented) {
            toggleSidebar();
          }
        }}
        className={cn("grid h-9 w-9 place-items-center rounded-lg transition", className)}
        {...props}
      >
        {children ?? <PanelLeft className="h-5 w-5" />}
      </button>
    );
  },
);
SidebarTrigger.displayName = "SidebarTrigger";

export { SidebarInset, SidebarProvider, SidebarTrigger, useSidebar };
