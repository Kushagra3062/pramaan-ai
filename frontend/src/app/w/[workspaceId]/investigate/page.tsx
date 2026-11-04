import { InvestigateView } from "@/components/workspace/InvestigateView";

export default async function WorkspaceInvestigatePage({ params }: Readonly<{ params: Promise<{ workspaceId: string }> }>) {
  const { workspaceId } = await params;
  return <InvestigateView workspaceId={workspaceId} />;
}
