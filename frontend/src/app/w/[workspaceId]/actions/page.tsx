import { ActionsView } from "@/components/workspace/ActionsView";

export default async function WorkspaceActionsPage({ params }: Readonly<{ params: Promise<{ workspaceId: string }> }>) {
  const { workspaceId } = await params;
  return <ActionsView workspaceId={workspaceId} />;
}
