import { SettingsView } from "@/components/workspace/SettingsView";

export default async function WorkspaceSettingsPage({ params }: Readonly<{ params: Promise<{ workspaceId: string }> }>) {
  const { workspaceId } = await params;
  return <SettingsView workspaceId={workspaceId} />;
}
