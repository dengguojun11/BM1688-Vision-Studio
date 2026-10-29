import { usePlatformProject } from '../project';
import RuleCenter from '../RuleCenter';
import { ModuleHeader } from '../shared';

export default function RulesPage() {
  const { projectId } = usePlatformProject();
  return (
    <>
      <ModuleHeader module="rules" />
      <RuleCenter projectId={projectId} />
    </>
  );
}
