// save = the CLI's would_save: what restoring a parked row would load
export type Row = { type: string; name: string; enabled: boolean; tokens: number; save: number; mod: boolean }

declare module 'claude-code' {
  interface PluginState {
    'agent-toggle': { rows: Row[]; busy: boolean; error: string; wheel: number }
  }
}
