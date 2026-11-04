import { WorkspaceShell } from "@/components/workspace/ui";

export default async function WorkspaceLayout({
  children,
  params,
}: Readonly<{ children: React.ReactNode; params: Promise<{ workspaceId: string }> }>) {
  const { workspaceId } = await params;
  return <WorkspaceShell workspaceId={workspaceId}>{children}</WorkspaceShell>;
}
