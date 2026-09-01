import {
  Tooltip as TooltipPrimitive,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

export function SimpleTooltip({
  children,
  content,
  ...props
}: {
  children: React.ReactNode;
  content?: React.ReactNode;
}) {
  return (
    <TooltipPrimitive delayDuration={500} {...props}>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent>{content}</TooltipContent>
    </TooltipPrimitive>
  );
}
