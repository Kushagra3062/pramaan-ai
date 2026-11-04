import { DocumentsView } from "@/components/workspace/DocumentsView";

export default async function WorkspaceDocumentsPage({ params }: Readonly<{ params: Promise<{ workspaceId: string }> }>) {
  const { workspaceId } = await params;
  return <DocumentsView workspaceId={workspaceId} />;
}
