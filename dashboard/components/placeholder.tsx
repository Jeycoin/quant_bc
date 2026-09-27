import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function Placeholder({
  title,
  phase,
  items,
}: {
  title: string;
  phase: string;
  items: string[];
}) {
  return (
    <Card className="bg-card/60">
      <CardHeader>
        <CardTitle className="text-sm">
          {title}
          <span className="ml-2 text-xs font-normal text-muted-foreground">{phase}</span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
          {items.map((i) => (
            <li key={i}>{i}</li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
