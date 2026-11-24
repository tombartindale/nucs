// Entry point Claude Desktop runs (see manifest.json): settings arrive as environment
// variables and, for the allowed folders, as arguments (one per folder).
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { createProxy } from './proxy.js';

const server = createProxy({
  url: process.env.NUCS_URL ?? '',
  token: process.env.NUCS_TOKEN ?? '',
  folders: process.argv.slice(2),
});
await server.connect(new StdioServerTransport());
