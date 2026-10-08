const fs = require('fs');
const path = require('path');

function walk(dir) {
  let results = [];
  const list = fs.readdirSync(dir);
  list.forEach(file => {
    const fullPath = path.join(dir, file);
    const stat = fs.statSync(fullPath);
    if (stat && stat.isDirectory()) {
      results = results.concat(walk(fullPath));
    } else if (file.endsWith('.jsx') || file.endsWith('.js')) {
      results.push(fullPath);
    }
  });
  return results;
}

const files = walk(path.resolve(__dirname, '../src'));
let issues = [];

files.forEach(file => {
  const content = fs.readFileSync(file, 'utf8');
  // Strip block comments and line comments
  const cleanContent = content.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*/g, '');

  // Extract all import declarations
  const importLines = cleanContent.match(/import\s+[\s\S]*?from\s+['"][^'"]+['"];?/g) || [];
  const importsText = importLines.join('\n');

  // Match JSX tags starting with capital letter
  const jsxTags = [...cleanContent.matchAll(/<([A-Z][a-zA-Z0-9_]*)/g)].map(m => m[1]);
  const uniqueTags = [...new Set(jsxTags)];

  uniqueTags.forEach(tag => {
    // Check if tag is imported or declared in file
    const isImported = new RegExp('\\b' + tag + '\\b').test(importsText);
    const isDeclared = new RegExp('(?:function|class|const|let|var)\\s+' + tag + '\\b').test(cleanContent);
    if (!isImported && !isDeclared) {
      issues.push({ file: path.relative(path.resolve(__dirname, '..'), file), tag });
    }
  });
});

console.log('Unresolved JSX tags found:');
console.log(JSON.stringify(issues, null, 2));
