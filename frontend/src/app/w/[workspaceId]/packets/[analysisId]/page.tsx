import { PacketView } from "@/components/workspace/InvestigateView";

export default async function WorkspacePacketPage({ params }: Readonly<{ params: Promise<{ workspaceId: string; analysisId: string }> }>) {
  const { workspaceId, analysisId } = await params;
  return <PacketView workspaceId={workspaceId} analysisId={analysisId} />;
}
