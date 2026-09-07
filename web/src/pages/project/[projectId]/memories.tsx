import { useEffect, useState } from "react";
import { BrainCircuit, Clock3, Database, RefreshCw, Tag } from "lucide-react";
import Page from "@/src/components/layouts/page";
import { Badge } from "@/src/components/ui/badge";
import { Button } from "@/src/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/src/components/ui/card";

type MemoryTag = { label: string; value: string; kind?: string };
type Memory = {
  id: string;
  memory: string;
  user: string;
  tags: MemoryTag[];
  session?: {
    id: string;
    title: string;
    started_at?: string | null;
  } | null;
};

export default function MemoriesPage() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [detail, setDetail] = useState<string>();

  const loadMemories = async () => {
    setStatus("loading");
    try {
      const response = await fetch(
        `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/api/memories`,
        { cache: "no-store" },
      );
      const body = (await response.json()) as {
        results?: Memory[];
        detail?: string;
      };
      if (!response.ok) {
        throw new Error(body.detail ?? "memory_catalog_unavailable");
      }
      setMemories(body.results ?? []);
      setDetail(undefined);
      setStatus("ready");
    } catch (error) {
      setDetail(
        error instanceof Error ? error.message : "memory_catalog_unavailable",
      );
      setStatus("error");
    }
  };

  useEffect(() => {
    void loadMemories();
  }, []);

  return (
    <Page
      headerProps={{
        title: "Memories",
        help: {
          description:
            "Review memories extracted from completed sessions by Mem0.",
        },
        actionButtonsRight: (
          <Button
            variant="outline"
            onClick={() => void loadMemories()}
            disabled={status === "loading"}
          >
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        ),
      }}
      scrollable
    >
      <div className="flex flex-col gap-4 p-4 md:p-6">
        <div className="grid gap-3 md:grid-cols-3">
          <Card>
            <CardContent className="flex items-center gap-3 p-4">
              <BrainCircuit className="h-5 w-5 text-primary" />
              <div>
                <p className="text-xs text-muted-foreground">
                  Extracted memories
                </p>
                <p className="text-2xl font-semibold">{memories.length}</p>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="flex items-center gap-3 p-4">
              <Database className="h-5 w-5 text-primary" />
              <div>
                <p className="text-xs text-muted-foreground">Pipeline</p>
                <p className="font-medium">Mem0 history worker</p>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="flex items-center gap-3 p-4">
              <Clock3 className="h-5 w-5 text-primary" />
              <div>
                <p className="text-xs text-muted-foreground">Scope</p>
                <p className="font-medium">All account memories</p>
              </div>
            </CardContent>
          </Card>
        </div>

        {status === "error" && (
          <Card>
            <CardContent className="flex items-center justify-between gap-4 p-5">
              <div>
                <p className="font-medium">Memory catalog unavailable</p>
                <p className="text-sm text-muted-foreground">{detail}</p>
              </div>
              <Button variant="outline" onClick={() => void loadMemories()}>
                Retry
              </Button>
            </CardContent>
          </Card>
        )}

        {status === "ready" && memories.length === 0 && (
          <Card>
            <CardContent className="flex flex-col items-center gap-2 p-12 text-center">
              <BrainCircuit className="h-8 w-8 text-muted-foreground" />
              <p className="font-medium">No extracted memories yet</p>
              <p className="text-sm text-muted-foreground">
                New completed sessions will appear here after the worker
                finishes extraction.
              </p>
            </CardContent>
          </Card>
        )}

        <div className="grid gap-4 xl:grid-cols-2">
          {memories.map((item) => (
            <Card key={item.id}>
              <CardHeader className="pb-3">
                <div className="flex items-start gap-3">
                  <div className="rounded-md bg-primary/10 p-2 text-primary">
                    <BrainCircuit className="h-4 w-4" />
                  </div>
                  <div className="min-w-0">
                    <CardTitle className="text-base leading-6">
                      {item.memory || "(empty memory)"}
                    </CardTitle>
                    <p className="mt-1 truncate text-xs text-muted-foreground">
                      {item.user} · {item.session?.title ?? "Unknown session"}
                    </p>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex flex-wrap gap-2">
                  {item.tags.map((tag) => (
                    <Badge
                      key={`${tag.label}-${tag.value}`}
                      variant="secondary"
                    >
                      <Tag className="mr-1 h-3 w-3" />
                      {tag.label} · {tag.value}
                    </Badge>
                  ))}
                </div>
                <div className="grid gap-2 rounded-md border bg-muted/20 p-3 text-xs text-muted-foreground sm:grid-cols-2">
                  <span>Session: {item.session?.id ?? "—"}</span>
                  <span className="truncate">Memory ID: {item.id}</span>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    </Page>
  );
}
