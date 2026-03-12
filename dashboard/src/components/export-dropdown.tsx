import { Download, FileJson, FileSpreadsheet } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { exportAsJson, exportAsCsv } from "@/lib/export";

interface ExportDropdownProps<T extends Record<string, unknown>> {
  data: T[];
  filename: string;
}

/**
 * Export dropdown button for list pages.
 * Offers JSON and CSV export of the currently displayed data.
 */
export function ExportDropdown<T extends Record<string, unknown>>({
  data,
  filename,
}: ExportDropdownProps<T>) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<Button variant="outline" size="sm" />}
      >
        <Download className="size-3" data-icon="inline-start" />
        Export
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={() => exportAsJson(data, filename)}>
          <FileJson className="size-3.5" />
          Export as JSON
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => exportAsCsv(data, filename)}>
          <FileSpreadsheet className="size-3.5" />
          Export as CSV
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
