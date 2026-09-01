---
name: "typescript-best-practices"
description: "TypeScript 最佳实践技能，包含类型安全、泛型、类型推断、严格模式配置。在编写 TypeScript 代码或类型定义时调用。"
---

# TypeScript Best Practices

## 配置

### tsconfig.json
```json
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

## 类型定义

### 接口 vs 类型别名
```typescript
// ✅ 使用 interface 定义对象形状
interface User {
  id: string;
  name: string;
  email: string;
}

// ✅ 使用 type 定义联合类型、元组
type Status = 'idle' | 'loading' | 'success' | 'error';
type Point = [number, number];
```

### 严格类型
```typescript
// ✅ 避免 any
function processData(data: unknown) {
  if (typeof data === 'string') {
    return data.toUpperCase();
  }
  throw new Error('Invalid data');
}

// ✅ 使用 unknown 替代 any
function parseJSON(json: string): unknown {
  return JSON.parse(json);
}
```

## 泛型

### 基础用法
```typescript
// ✅ 泛型函数
function identity<T>(arg: T): T {
  return arg;
}

// ✅ 泛型接口
interface Container<T> {
  value: T;
  getValue(): T;
}

// ✅ 泛型约束
interface HasLength {
  length: number;
}

function logLength<T extends HasLength>(arg: T): T {
  console.log(arg.length);
  return arg;
}
```

### 高级泛型
```typescript
// ✅ 条件类型
type NonNullable<T> = T extends null | undefined ? never : T;

// ✅ 映射类型
type Readonly<T> = {
  readonly [P in keyof T]: T[P];
};

// ✅ 工具类型
type Pick<T, K extends keyof T> = {
  [P in K]: T[P];
};

type Omit<T, K extends keyof T> = Pick<T, Exclude<keyof T, K>>;
```

## React + TypeScript

### 组件 Props
```typescript
// ✅ 使用 interface 定义 Props
interface ButtonProps {
  variant?: 'primary' | 'secondary';
  size?: 'sm' | 'md' | 'lg';
  disabled?: boolean;
  children: React.ReactNode;
  onClick?: (event: React.MouseEvent<HTMLButtonElement>) => void;
}

// ✅ FC 类型（可选）
export const Button: React.FC<ButtonProps> = ({ 
  variant = 'primary',
  children,
  onClick 
}) => {
  return <button onClick={onClick}>{children}</button>;
};
```

### Hooks 类型
```typescript
// ✅ useState 类型推断
const [count, setCount] = useState<number>(0);

// ✅ useRef 类型
const inputRef = useRef<HTMLInputElement>(null);

// ✅ useCallback 类型
const handleClick = useCallback(() => {
  console.log('clicked');
}, []);

// ✅ 自定义 Hook
function useLocalStorage<T>(key: string, initialValue: T): [T, (value: T) => void] {
  const [storedValue, setStoredValue] = useState<T>(() => {
    try {
      const item = window.localStorage.getItem(key);
      return item ? JSON.parse(item) : initialValue;
    } catch {
      return initialValue;
    }
  });

  const setValue = (value: T) => {
    setStoredValue(value);
    window.localStorage.setItem(key, JSON.stringify(value));
  };

  return [storedValue, setValue];
}
```

## 类型守卫

```typescript
// ✅ typeof 类型守卫
function process(value: string | number) {
  if (typeof value === 'string') {
    return value.toUpperCase();
  }
  return value.toFixed(2);
}

// ✅ instanceof 类型守卫
function logError(error: Error | string) {
  if (error instanceof Error) {
    console.error(error.message);
  } else {
    console.error(error);
  }
}

// ✅ 自定义类型守卫
interface Cat {
  type: 'cat';
  meow(): void;
}

interface Dog {
  type: 'dog';
  bark(): void;
}

function isCat(animal: Cat | Dog): animal is Cat {
  return animal.type === 'cat';
}
```

## 最佳实践

1. **启用严格模式** - `strict: true`
2. **避免 any** - 使用 `unknown` 或具体类型
3. **使用类型推断** - 让 TypeScript 自动推断简单类型
4. **明确返回类型** - 公共函数标注返回类型
5. **使用常量断言** - `as const` 创建不可变类型
6. **类型导出** - 公共类型从模块导出

## 常见模式

```typescript
// ✅ 可选链和空值合并
const name = user?.profile?.name ?? 'Anonymous';

// ✅ 非空断言（谨慎使用）
const element = document.getElementById('app')!;

// ✅ 索引签名
interface Dictionary {
  [key: string]: number;
}

// ✅ 只读数组
const numbers: readonly number[] = [1, 2, 3];

// ✅ 元组
const tuple: [string, number] = ['age', 25];
```
