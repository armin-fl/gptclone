import * as React from "react";

import { cn } from "@/lib/utils";

const ScrollArea = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => {
    return <div ref={ref} className={cn("overflow-y-auto", className)} {...props} />;
  },
);

ScrollArea.displayName = "ScrollArea";

export { ScrollArea };
