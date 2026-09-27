import { Placeholder } from "@/components/placeholder";

export default function ExecutorsPage() {
  return (
    <Placeholder
      title="Executors"
      phase="Phase 3"
      items={[
        "Executor table: id / type / symbol / status / side / PnL",
        "Detail drawer with orders and filled orders",
        "Grid executors: range and level visualization",
      ]}
    />
  );
}
