import ts from 'typescript';

/** The public SDK source is the only authority for its runtime exports. */
export function sdkRuntimeExports(program, filename) {
  const checker = program.getTypeChecker();
  const module = checker.getSymbolAtLocation(program.getSourceFile(filename));
  return checker.getExportsOfModule(module).filter(symbol => {
    const value = symbol.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(symbol) : symbol;
    return Boolean(value.flags & ts.SymbolFlags.Value);
  }).map(symbol => symbol.name).sort();
}
