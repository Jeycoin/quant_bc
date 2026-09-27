import { Timeline } from "@/components/timeline";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function TimelinePage() {
  return (
    <Card className="bg-card/60">
      <CardHeader>
        <CardTitle className="text-sm">
          AI Decision Timeline
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            structured decision summaries only — no chain-of-thought
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <Timeline limit={100} />
      </CardContent>
    </Card>
  );
}
