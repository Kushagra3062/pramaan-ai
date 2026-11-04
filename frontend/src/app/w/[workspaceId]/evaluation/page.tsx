import { EvaluationView } from "@/components/workspace/EvaluationView";

export default async function WorkspaceEvaluationPage({ params }: Readonly<{ params: Promise<{ workspaceId: string }> }>) {
  const { workspaceId } = await params;
  return <EvaluationView workspaceId={workspaceId} />;
}
