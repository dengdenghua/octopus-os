import {
  MentionAutocompletePopup,
  type MentionItem,
} from "../mention-autocomplete";

interface MentionPickerProps {
  isOpen: boolean;
  items: MentionItem[];
  selectedIndex: number;
  isLoading: boolean;
  mentionQuery: string;
  onSelect: (item: MentionItem) => void;
}

export function MentionPicker({
  isOpen,
  items,
  selectedIndex,
  isLoading,
  mentionQuery,
  onSelect,
}: MentionPickerProps) {
  if (!isOpen) return null;
  return (
    <MentionAutocompletePopup
      items={items}
      selectedIndex={selectedIndex}
      isLoading={isLoading}
      mentionQuery={mentionQuery}
      onSelect={onSelect}
    />
  );
}
