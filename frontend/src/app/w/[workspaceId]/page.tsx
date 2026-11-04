import { DashboardView } from "@/components/workspace/DashboardView";

export default async function WorkspaceDashboardPage({ params }: Readonly<{ params: Promise<{ workspaceId: string }> }>) {
  const { workspaceId } = await params;
  return <DashboardView workspaceId={workspaceId} />;
}
