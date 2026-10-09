import type { FrontendPlugin } from '../../../frontend/src/plugins/sdk';
import { PackDesigner, FaceDesigner, FunctionDesigner, Printer, Packer } from '../../../frontend/src/factory/FactoryViews';

export default { apiVersion: 1, views: { pack: PackDesigner, face: FaceDesigner, function: FunctionDesigner, printer: Printer, packer: Packer } } satisfies FrontendPlugin;
