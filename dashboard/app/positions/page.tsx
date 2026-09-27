import { Placeholder } from "@/components/placeholder";

export default function PositionsPage() {
  return (
    <Placeholder
      title="Positions"
      phase="Phase 2"
      items={[
        "Symbol / side / quantity / entry / current price",
        "Unrealized and realized PnL",
        "Live mark price updates",
      ]}
    />
  );
}
