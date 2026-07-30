import { TrashIcon, XMarkIcon } from '@heroicons/react/24/outline';

export function PanelHeader({ title, onClose }: { title: string; onClose: () => void }) {
  return (
    <div className="flex items-center justify-between px-6 py-4 border-b border-surface-700">
      <h2 className="font-display font-semibold text-lg text-surface-100">{title}</h2>
      <button
        onClick={onClose}
        className="p-2 text-surface-400 hover:text-surface-200 hover:bg-surface-700 rounded-lg transition-colors"
      >
        <XMarkIcon className="w-5 h-5" />
      </button>
    </div>
  );
}

export function PanelFooter({
  onSave,
  onDelete,
  saveDisabled
}: {
  onSave: () => void;
  onDelete?: () => void;
  saveDisabled?: boolean;
}) {
  return (
    <div className="flex items-center justify-between px-6 py-4 border-t border-surface-700">
      {onDelete ? (
        <button onClick={onDelete} className="btn-danger">
          <TrashIcon className="w-4 h-4" />
          删除
        </button>
      ) : (
        <div />
      )}
      <button
        onClick={onSave}
        className="btn-primary"
        disabled={saveDisabled}
      >
        应用到画布
      </button>
    </div>
  );
}
