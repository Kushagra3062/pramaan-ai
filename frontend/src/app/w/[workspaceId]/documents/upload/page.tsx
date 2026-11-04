import { UploadView } from "@/components/workspace/UploadView";

export default async function WorkspaceUploadPage({ params }: Readonly<{ params: Promise<{ workspaceId: string }> }>) {
  const { workspaceId } = await params;
  return <UploadView workspaceId={workspaceId} />;
}
