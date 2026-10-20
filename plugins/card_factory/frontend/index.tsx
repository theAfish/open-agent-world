import type { FrontendPlugin } from '@oaw/plugin-api';
import { PackDesigner, FaceDesigner, FunctionDesigner, Printer, Packer } from '@oaw/plugin-api';

export default { apiVersion: 1, views: { pack: PackDesigner, face: FaceDesigner, function: FunctionDesigner, printer: Printer, packer: Packer } } satisfies FrontendPlugin;
