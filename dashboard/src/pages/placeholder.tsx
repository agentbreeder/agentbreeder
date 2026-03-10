import { useLocation } from "react-router-dom";
import { Construction } from "lucide-react";

export default function PlaceholderPage() {
  const { pathname } = useLocation();
  const name = pathname.split("/").filter(Boolean).pop() ?? "Page";

  return (
    <div className="flex flex-col items-center justify-center py-32 text-center">
      <div className="mb-4 flex size-14 items-center justify-center rounded-2xl border border-dashed border-border">
        <Construction className="size-6 text-muted-foreground" />
      </div>
      <h2 className="text-base font-semibold capitalize">{name}</h2>
      <p className="mt-1 max-w-xs text-xs text-muted-foreground">
        This section is under construction. Coming in M4.2.
      </p>
    </div>
  );
}
