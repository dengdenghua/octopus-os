---
name: "code-quality"
description: "代码质量技能，包含代码审查、重构、性能优化、最佳实践检查。在代码审查或重构时调用。"
---

# Code Quality

## Code Review Checklist

### 1. 可读性
- [ ] 命名清晰有意义
- [ ] 函数短小精悍（< 20 行）
- [ ] 注释解释"为什么"而非"是什么"
- [ ] 避免嵌套过深（< 3 层）

### 2. 可维护性
- [ ] 单一职责原则
- [ ] DRY (Don't Repeat Yourself)
- [ ] 开闭原则
- [ ] 依赖注入

### 3. 性能
- [ ] 避免不必要的渲染
- [ ] 懒加载大组件
- [ ] 图片优化
- [ ] 代码分割

### 4. 安全性
- [ ] 输入验证
- [ ] XSS 防护
- [ ] CSRF 防护
- [ ] 敏感信息不硬编码

### 5. 测试
- [ ] 单元测试覆盖
- [ ] 边界条件测试
- [ ] 错误处理测试
- [ ] 集成测试

## Refactoring Patterns

### Extract Function
```typescript
// Before
function processOrder(order: Order) {
  // Validate
  if (!order.items.length) throw new Error('Empty order');
  if (order.total <= 0) throw new Error('Invalid total');
  
  // Calculate
  const tax = order.total * 0.1;
  const shipping = order.total > 100 ? 0 : 10;
  
  // Save
  database.save(order);
  email.send(order.customer, 'Order confirmed');
}

// After
function processOrder(order: Order) {
  validateOrder(order);
  const pricing = calculatePricing(order);
  saveOrder(order, pricing);
}

function validateOrder(order: Order) {
  if (!order.items.length) throw new Error('Empty order');
  if (order.total <= 0) throw new Error('Invalid total');
}

function calculatePricing(order: Order) {
  return {
    tax: order.total * 0.1,
    shipping: order.total > 100 ? 0 : 10,
  };
}

function saveOrder(order: Order, pricing: Pricing) {
  database.save({ ...order, ...pricing });
  email.send(order.customer, 'Order confirmed');
}
```

### Replace Conditional with Polymorphism
```typescript
// Before
function calculateArea(shape: Shape) {
  if (shape.type === 'circle') {
    return Math.PI * shape.radius ** 2;
  } else if (shape.type === 'rectangle') {
    return shape.width * shape.height;
  }
}

// After
interface Shape {
  calculateArea(): number;
}

class Circle implements Shape {
  constructor(private radius: number) {}
  calculateArea() {
    return Math.PI * this.radius ** 2;
  }
}

class Rectangle implements Shape {
  constructor(private width: number, private height: number) {}
  calculateArea() {
    return this.width * this.height;
  }
}
```

## Performance Optimization

### React
- 使用 React.memo 避免不必要渲染
- 使用 useMemo/useCallback 缓存计算
- 虚拟列表处理大数据
- 代码分割和懒加载

### JavaScript
- 避免内存泄漏
- 使用 requestAnimationFrame 优化动画
- 防抖和节流高频操作
- Web Workers 处理复杂计算

### CSS
- 使用 transform 和 opacity 做动画
- 避免触发重排的属性
- 使用 CSS 变量统一管理
- 压缩和合并 CSS

## Common Anti-patterns

### 1. 上帝对象
```typescript
// ❌ Bad
class GodObject {
  // 处理用户
  // 处理订单
  // 处理支付
  // 处理邮件
  // ...
}

// ✅ Good
class UserService { }
class OrderService { }
class PaymentService { }
class EmailService { }
```

### 2. 魔术数字
```typescript
// ❌ Bad
if (status === 3) { }

// ✅ Good
const STATUS_COMPLETED = 3;
if (status === STATUS_COMPLETED) { }
```

### 3. 深层嵌套
```typescript
// ❌ Bad
if (user) {
  if (user.profile) {
    if (user.profile.address) {
      return user.profile.address.city;
    }
  }
}

// ✅ Good
return user?.profile?.address?.city;
```

## Tools

### Linting
- ESLint - 代码规范
- Prettier - 代码格式化
- Stylelint - CSS 规范

### Testing
- Jest - 单元测试
- React Testing Library - 组件测试
- Cypress - E2E 测试
- Playwright - E2E 测试

### Analysis
- SonarQube - 代码质量分析
- CodeClimate - 代码质量监控
- Bundle Analyzer - 包大小分析
