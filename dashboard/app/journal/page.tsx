import { Placeholder } from "@/components/placeholder";

export default function JournalPage() {
  return (
    <Placeholder
      title="Trade Journal"
      phase="Phase 6"
      items={[
        "Historical trades: entry / exit / PnL / duration",
        "Original decision summary + market context + review",
        "Filter by symbol / strategy / result / date",
      ]}
    />
  );
}
