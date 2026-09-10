<script lang="ts">
  import { Structure, type AnyStructure } from 'matterviz/structure';
  let { structure, onError }: { structure: AnyStructure; onError: (message: string) => void } = $props();
</script>

<svelte:boundary onerror={(error) => onError(error instanceof Error ? error.message : String(error))}>
  <Structure {structure} allow_file_drop={false} analyze_symmetry={false} show_controls="always"
    style="width:100%;height:100%;min-width:0;min-height:280px" performance_mode="speed" background_color="#f4f5f2"
    on_error={(event) => onError(event.error_msg ?? 'Unable to render this structure')} />
  {#snippet failed()}<span></span>{/snippet}
</svelte:boundary>
