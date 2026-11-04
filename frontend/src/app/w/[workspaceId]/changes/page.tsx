import { ChangesView } from "@/components/workspace/ChangesView";

export default async function WorkspaceChangesPage({ params }: Readonly<{ params: Promise<{ workspaceId: string }> }>) {
  const { workspaceId } = await params;
  return <ChangesView workspaceId={workspaceId} />;
}
