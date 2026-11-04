import { DocumentDetailView } from "@/components/workspace/DocumentDetailView";

export default async function WorkspaceDocumentDetailPage({ params }: Readonly<{ params: Promise<{ workspaceId: string; documentId: string }> }>) {
  const { workspaceId, documentId } = await params;
  return <DocumentDetailView workspaceId={workspaceId} documentId={documentId} />;
}
