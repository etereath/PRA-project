# 开放API

> 本文档包含「开放API」分类下的所有文档内容



================================================================================
## 文档路径: 开放API/API接口/鉴权
================================================================================

# 鉴权
路径: 开放API/API接口/鉴权


# 鉴权


## 前置操作

使用企业管理员账号登录影刀控制台，在api配置界面新增**accessKeyId**和**accessKeySecret**，取后请妥善保管，管理员可以给每个需要对接影刀的系统自创建一对密钥。

**说明：** accessToken的最大有效期是 2 小时。当accessToken未过期时，请求会返回老的accessToken，当accessToken已过期时，，accessToken是一个临时token，请求会返回新的token，调用方可以不用缓存，如果需要缓存，可以根据请求token时返回的 expiresIn 字段进行缓存，该字段此次获取token的有效期，单位是秒(不要自定义缓存时间， 可能会导致我方token和调用方缓存的有效期不一致)。任务运行完成就会失效。


## 模板


### postMan模板

授权创建accessToken.json（右键另存为）


### Java模板

无


## 请求

|  |
|  |
| **HTTP URL** | https://api.yingdao.com/oapi/token/v2/token/create | 专有云企业请使用专有云地址 |
| **HTTP Method** | GET |  |

**基本**

**参数值**

**说明**

**HTTP URL**

https://api.yingdao.com/oapi/token/v2/token/create

专有云企业请使用专有云地址

**HTTP Method**

GET




### 请求示例

http://xxx/oapi/token/v2/token/create?accessKeyId=MerC5cKPSa7BTG1A@platform&accessKeySecret=mqTxhk4aK1v7PpDtfQU6dCMgnrR50HFc


## 响应


### 响应体

|  |
|  |
| **code** | int | 是 |
| **success** | boolean | 是 |
| **msg** | string | 是 |
| **data** | object | 是 |
| **∟ accessToken** | string | 是 |
| **∟ expiresIn** | int | 是 |

**名称**

**类型**

**是否必填**

**code**

int

是

**success**

boolean

是

**msg**

string

是

**data**

object

是

**∟ accessToken**

string

是

**∟ expiresIn**

int

是


#### 响应体示例


```None
{
    "data": {
        "accessToken": "520da9c9-694d-4b40-9332-0c179243c88e",
        "expiresIn": 7199
    },
    "code": 200,
    "success": true,
    "requestId": "601cf6274032e2cc335c97d2"
}
```


## 使用accessToken

accessToken 在每次调用具体的接口的时候，需要作为参数放在 header 中，参数放置如下：

Authorization: Bearer ${accessToken}， 注意，需要前置加一个Bearer, Bearer和accessToken之间需要用空格隔开

如遇到错误，请跳转到 状态码说明


================================================================================
## 文档路径: 开放API/API接口/RPA企业账号/查询RPA企业账号列表
================================================================================

# 查询RPA企业账号列表
路径: 开放API/API接口/RPA企业账号/查询RPA企业账号列表


# **查询RPA企业账号列表**

该接口用于获取企业下符合条件的所有rpa企业账号数据。 说明：调用接口的鉴权账号需有调度管理员权限。

一次调用最多可返回100个账号，可通过分页多次调用获取所有账号。


## **前置操作**

1. 使用鉴权接口获取accessToken。

使用鉴权接口获取accessToken。


## **请求**

|  |
|  |
| **HTTP URL** | https://api.yingdao.com/oapi/rpa/user/v1/list | 专有云企业请使用专有云地址 |
| **HTTP Method** | GET |  |

**基本**

**参数值**

**说明**

**HTTP URL**

https://api.yingdao.com/oapi/rpa/user/v1/list

专有云企业请使用专有云地址

**HTTP Method**

GET



### **请求头**

|  |
|  |
| **Authorization** | Bearer {accessToken} | {accessToken}变量需要替换成鉴权接口返回的access Token |

**基本**

**参数值**

**说明**

**Authorization**

Bearer {accessToken}

{accessToken}变量需要替换成鉴权接口返回的access Token


### **请求参数**

|  |
|  |
| **phone** | String | 否 | 完整的手机号 |
| **accountKeyword** | String | 否 | 账号关键词 |
| **latestLoginTimeBegin** | Long | 否 | 最后登录时间左边界，秒级时间戳 |
| **latestLoginTimeEnd** | Long | 否 | 最近登录时间右边界，秒级时间戳 |
| **expiredTimeBegin** | Long | 否 | 过期时间左边界，秒级时间戳 |
| **expiredTimeEnd** | Long | 否 | 过期时间右边界，秒级时间戳 |
| **accountTypes** | List<String> | 否 | 账号类型列表，基础账号-basic，高级账号-senior |
| **page** | int | 否 | 页码 |
| **size** | int | 否 | 一页默认20条，最大支持100 |

**名称**

**类型**

**是否必填**

**说明**

**phone**

String

否

完整的手机号

**accountKeyword**

String

否

账号关键词

**latestLoginTimeBegin**

Long

否

最后登录时间左边界，秒级时间戳

**latestLoginTimeEnd**

Long

否

最近登录时间右边界，秒级时间戳

**expiredTimeBegin**

Long

否

过期时间左边界，秒级时间戳

**expiredTimeEnd**

Long

否

过期时间右边界，秒级时间戳

**accountTypes**

List<String>

否

账号类型列表，基础账号-basic，高级账号-senior

**page**

int

否

页码

**size**

int

否

一页默认20条，最大支持100


## **响应**


### **响应数据结构**

|  |
|  |
| **data** | array | 结果数据 |
| **  |─** | object |  |
| **    |─userUuid** | string | 用户uuid |
| **    |─loginAccount** | string | 登录账户 |
| **    |─name** | string | 账户名称 |
| **    |─phone** | string | 手机号 |
| **    |─role** | string | 角色 |
| **    |─roleName** | string | 角色名称 |
| **    |─accountType** | string | 帐户类型 |
| **    |─accountTypeName** | string | 帐户类型名称 |
| **    |─latestLoginTime** | string | 最近登录时间 |
| **    |─expiredTime** | string | 过期时间 |
| **page** | object | 分页数据 |
| **  |─total** | integer | 总条数 |
| **  |─size** | integer | 每页条数 |
| **  |─page** | integer | 当前页 |
| **  |─pages** | integer | 总页数 |
| **  |─offset** | integer | 偏移量 |
| **  |─sortColumn** | string | 用于排序的 column 的名称 |
| **  |─order** | string | 排序方式 desc/asc |
| **code** | integer | 返回结果编码200表示成功，其他表示失败 |
| **success** | boolean | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **requestId** | string | 请求id，方便排查使用 |
| **serverIp** | string | 处理服务的Ip |
| **serverInstName** | string | 处理服务实例名称 |
| **msg** | string | 状态码描述 |

**名称**

**类型**

**说明**

**data**

array

结果数据

**  |─**

object


**    |─userUuid**

string

用户uuid

**    |─loginAccount**

string

登录账户

**    |─name**

string

账户名称

**    |─phone**

string

手机号

**    |─role**

string

角色

**    |─roleName**

string

角色名称

**    |─accountType**

string

帐户类型

**    |─accountTypeName**

string

帐户类型名称

**    |─latestLoginTime**

string

最近登录时间

**    |─expiredTime**

string

过期时间

**page**

object

分页数据

**  |─total**

integer

总条数

**  |─size**

integer

每页条数

**  |─page**

integer

当前页

**  |─pages**

integer

总页数

**  |─offset**

integer

偏移量

**  |─sortColumn**

string

用于排序的 column 的名称

**  |─order**

string

排序方式 desc/asc

**code**

integer

返回结果编码200表示成功，其他表示失败

**success**

boolean

调用是否成功，可以根据该字段判断接口调用是否成功

**requestId**

string

请求id，方便排查使用

**serverIp**

string

处理服务的Ip

**serverInstName**

string

处理服务实例名称

**msg**

string

状态码描述


### **响应数据案例**


```None
{
    "data": [
        {
            "userUuid": "xxxuuid",
            "loginAccount": "xxxacc@abbr",
            "name": "xxxname",
            "phone": "13312345678",
            "role": "e_user",
            "roleName": "员工",
            "accountType": "basic",
            "accountTypeName": "基础账号",
            "latestLoginTime": "2024-02-28 15:21:51",
            "expiredTime": "2025-01-19 23:59:59"
        }
    ],
    "page": {
        "total": 1,
        "size": 20,
        "page": 1,
        "pages": 1,
        "offset": 0,
        "order": "desc"
    },
    "code": 200,
    "success": true,
    "requestId": "44265d53-842b-4a7f-9174-6bd900ebaa91"
}
```


## **使用示例**


### **curl示例**


```None
curl --location --request GET 'https://api.yingdao.com/oapi/rpa/user/v1/list?latestLoginTimeBegin=1680192000&latestLoginTimeEnd=1706630400&expiredTimeBegin=1678550400&expiredTimeEnd=1741708800&accountTypes=basic,senior&page=1&size=20' \
--header 'Authorization: 替换为鉴权接口返回的accessToken'
```


### **Java示例**

案例使用Unirest库。


```None
String accessToken="替换为鉴权接口返回的access Token";
HttpResponse<String> response = Unirest.get("https://api.yingdao.com/oapi/rpa/user/v1/list?latestLoginTimeBegin=1680192000&latestLoginTimeEnd=1706630400&expiredTimeBegin=1678550400&expiredTimeEnd=1741708800&accountTypes=basic,senior&page=1&size=20")
.header("Authorization",String.format("Bearer %s", accessToken)).asString();
System.out.println(response.getBody());
```


================================================================================
## 文档路径: 开放API/API接口/RPA企业账号/创建RPA企业账号
================================================================================

# 创建RPA企业账号
路径: 开放API/API接口/RPA企业账号/创建RPA企业账号


# 创建RPA企业账号

该接口用于创建RPA企业账号


## 前置操作

1. 使用鉴权接口获取accessToken。

使用鉴权接口获取accessToken。


## 请求

|  |
|  |
| **HTTP URL** | https://api.yingdao.com/oapi/rpa/user/v1/create |  |
| **HTTP Method** | POST |  |

**基本**

**参数值**

**说明**

**HTTP URL**

https://api.yingdao.com/oapi/rpa/user/v1/create


**HTTP Method**

POST



### 请求头

|  |
|  |
| **Authorization** | Bearer {accessToken} | {accessToken}变量需要替换成鉴权接口返回的access Token |

**基本**

**参数值**

**说明**

**Authorization**

Bearer {accessToken}

{accessToken}变量需要替换成鉴权接口返回的access Token


### 请求参数

|  |
|  |
| **name** | string | 员工姓名, 必填 |
| **account** | string | 账号, 必填 |
| **phone** | string | 手机号, 必填 |
| **email** | string | 邮箱, 可选 |
| **accountType** | string | 账号类型: basic 基础账号, senior 高级账号, 必填 |
| **userRole** | string | 用户角色: e_admin 管理员, e_user 员工, 必填 |
| **password** | string | 密码, 必填 |

**名称**

**类型**

**说明**

**name**

string

员工姓名, 必填

**account**

string

账号, 必填

**phone**

string

手机号, 必填

**email**

string

邮箱, 可选

**accountType**

string

账号类型: basic 基础账号, senior 高级账号, 必填

**userRole**

string

用户角色: e_admin 管理员, e_user 员工, 必填

**password**

string

密码, 必填



## 响应


### 响应数据结构

|  |
|  |
| **data** | object | 结果数据 |
| **    |─userUuid** | string |  |
| **    |─name** | string |  |
| **    |─account** | string |  |
| **code** | integer | 返回结果编码 |
| **success** | boolean | true 表示成功，false 表示失败 |
| **requestId** | string |  |
| **msg** | string | 结果信息 |

**名称**

**类型**

**说明**

**data**

object

结果数据

**    |─userUuid**

string


**    |─name**

string


**    |─account**

string


**code**

integer

返回结果编码

**success**

boolean

true 表示成功，false 表示失败

**requestId**

string


**msg**

string

结果信息


## 补充说明

- code 说明

code 说明

|  |
|  |
| **200** | 成功 |
| **400** | 缺少参数(参数错误) |
| **481** | 账号额度不足 |
| **20010009** | RPA企业账号已存在 |
| **20010022** | 不支持的账号类型 |
| **20010033** | 不支持的角色 |

**code**

**说明**

**200**

成功

**400**

缺少参数(参数错误)

**481**

账号额度不足

**20010009**

RPA企业账号已存在

**20010022**

不支持的账号类型

**20010033**

不支持的角色



## 示例


### 创建高级账号-管理员


```None
curl --location --request POST 'https://api.yingdao.com/oapi/rpa/user/v1/create' \
--header 'Authorization: Bearer f62edeac-c4f3-4b34-afcf-8fc9b5ecf33c' \
--header 'User-Agent: Apifox/1.0.0 (https://apifox.com)' \
--header 'Content-Type: application/json' \
--data-raw '{
    "name": "test0514-01",
    "account": "test0514-01",
    "phone": "13866668888",
    "email": "test@yingdao.com",
    "accountType": "senior",
    "userRole": "e_admin",
    "password": "123456"
}'
```

返回值


```None
{
    "data": {
        "userUuid": "686397873057394688",
        "name": "test0514-01",
        "account": "test0514-01",
        "loginAccount": "test0514-01@fckj"
    },
    "code": 200,
    "success": true,
    "requestId": "1105f409-4c96-47ff-ae13-e2ff6877ddac"
}
```


### 创建基础账号-管理员


```None
curl --location --request POST 'https://api.yingdao.com/oapi/rpa/user/v1/create' \
--header 'Authorization: Bearer f62edeac-c4f3-4b34-afcf-8fc9b5ecf33c' \
--header 'User-Agent: Apifox/1.0.0 (https://apifox.com)' \
--header 'Content-Type: application/json' \
--data-raw '{
    "name": "test0514-02",
    "account": "test0514-02",
    "phone": "13855558888",
    "email": "test01@yingdao.com",
    "accountType": "basic",
    "userRole": "e_admin",
    "password": "123456"
}'
```

返回值


```None
{
    "data": {
        "userUuid": "686398096118870016",
        "name": "test0514-02",
        "account": "test0514-02",
        "loginAccount": "test0514-02@fckj"
    },
    "code": 200,
    "success": true,
    "requestId": "1100b85c-2a43-427d-9ab7-62f5a255b460"
}
```


### 创建高级账号-员工


```None
curl --location --request POST 'https://api.yingdao.com/oapi/rpa/user/v1/create' \
--header 'Authorization: Bearer f62edeac-c4f3-4b34-afcf-8fc9b5ecf33c' \
--header 'User-Agent: Apifox/1.0.0 (https://apifox.com)' \
--header 'Content-Type: application/json' \
--data-raw '{
    "name": "test0514-03",
    "account": "test0514-03",
    "phone": "13866668888",
    "email": "test@yingdao.com",
    "accountType": "senior",
    "userRole": "e_user",
    "password": "123456"
}'
```

返回值


```None
{
    "data": {
        "userUuid": "686399932506796032",
        "name": "test0514-03",
        "account": "test0514-03",
        "loginAccount": "test0514-03@fckj"
    },
    "code": 200,
    "success": true,
    "requestId": "afb3b650-cfe6-4c4b-b3f2-91c3c49189e8"
}
```


### 创建基础账号-员工


```None
curl --location --request POST 'https://api.yingdao.com/oapi/rpa/user/v1/create' \
--header 'Authorization: Bearer f62edeac-c4f3-4b34-afcf-8fc9b5ecf33c' \
--header 'User-Agent: Apifox/1.0.0 (https://apifox.com)' \
--header 'Content-Type: application/json' \
--data-raw '{
    "name": "test0514-04",
    "account": "test0514-04",
    "phone": "13855558888",
    "email": "test01@yingdao.com",
    "accountType": "basic",
    "userRole": "e_user",
    "password": "123456"
}'
```

返回值


```None
{
    "data": {
        "userUuid": "686400339870183424",
        "name": "test0514-04",
        "account": "test0514-04",
        "loginAccount": "test0514-04@fckj"
    },
    "code": 200,
    "success": true,
    "requestId": "f8ac81d7-9940-4d2f-839c-d13a0fd8c537"
}
```


================================================================================
## 文档路径: 开放API/API接口/RPA企业账号/修改RPA企业账号
================================================================================

# 修改RPA企业账号
路径: 开放API/API接口/RPA企业账号/修改RPA企业账号


# 修改RPA企业账号

该接口用于修改RPA企业账号


## 前置操作

1. 使用鉴权接口获取accessToken。

使用鉴权接口获取accessToken。


## 请求

|  |
|  |
| **HTTP URL** | https://api.yingdao.com/oapi/rpa/user/v1/modify |  |
| **HTTP Method** | POST |  |

**基本**

**参数值**

**说明**

**HTTP URL**

https://api.yingdao.com/oapi/rpa/user/v1/modify


**HTTP Method**

POST



### 请求头

|  |
|  |
| **Authorization** | Bearer {accessToken} | {accessToken}变量需要替换成鉴权接口返回的access Token |

**基本**

**参数值**

**说明**

**Authorization**

Bearer {accessToken}

{accessToken}变量需要替换成鉴权接口返回的access Token


### 请求参数

|  |
|  |
| **account** | string | 账号, （确定需要修改的账号）, 必传 |
| **name** | string | 员工姓名, 传空(或者空串或者不传)表示不修改 |
| **phone** | string | 手机号, 传空(或者空串或者不传)表示不修改 |
| **email** | string | 邮箱, 传空(或者不传或者传空串)表示不修改 |
| **userRole** | string | 用户角色: e_admin 管理员, e_user 员工, 传空(或者空串或者不传)表示不修改 |

**名称**

**类型**

**说明**

**account**

string

账号, （确定需要修改的账号）, 必传

**name**

string

员工姓名, 传空(或者空串或者不传)表示不修改

**phone**

string

手机号, 传空(或者空串或者不传)表示不修改

**email**

string

邮箱, 传空(或者不传或者传空串)表示不修改

**userRole**

string

用户角色: e_admin 管理员, e_user 员工, 传空(或者空串或者不传)表示不修改



## 响应


### 响应数据结构

|  |
|  |
| **data** | object | 结果数据 |
| **    |─userUuid** | string |  |
| **code** | integer | 返回结果编码 |
| **success** | boolean | true 表示成功，false 表示失败 |
| **requestId** | string |  |
| **msg** | string | 结果信息 |

**名称**

**类型**

**说明**

**data**

object

结果数据

**    |─userUuid**

string


**code**

integer

返回结果编码

**success**

boolean

true 表示成功，false 表示失败

**requestId**

string


**msg**

string

结果信息


## 补充说明

- code说明

code说明

|  |
|  |
| **200** | 成功 |
| **400** | 缺少参数(参数错误) |
| **481** | 账号额度不足 |
| **20010009** | RPA企业账号已存在 |
| **20010022** | 不支持的账号类型 |
| **20010033** | 不支持的角色 |

**code**

**说明**

**200**

成功

**400**

缺少参数(参数错误)

**481**

账号额度不足

**20010009**

RPA企业账号已存在

**20010022**

不支持的账号类型

**20010033**

不支持的角色



## 示例


### 修改员工姓名


```None
curl --location --request POST 'https://api.yingdao.com/oapi/rpa/user/v1/modify' \
--header 'Authorization: Bearer 0de55cf0-b0bb-4263-bcc2-951720966624' \
--header 'User-Agent: Apifox/1.0.0 (https://apifox.com)' \
--header 'Content-Type: application/json' \
--data-raw '{
    "account": "test-0514@fckj",
    "name": "test-0514-01"
}'
```

返回值


```None
{
    "data": {
        "userUuid": "686391060106801152"
    },
    "code": 200,
    "success": true,
    "requestId": "be36f100-ea22-4d97-8f09-159ef617e03c"
}
```


### 修改手机号


```None
curl --location --request POST 'https://api.yingdao.com/oapi/rpa/user/v1/modify' \
--header 'Authorization: Bearer 0de55cf0-b0bb-4263-bcc2-951720966624' \
--header 'User-Agent: Apifox/1.0.0 (https://apifox.com)' \
--header 'Content-Type: application/json' \
--data-raw '{
    "account": "test-0514@fckj",
    "phone": "13988886666"
}'
```

返回值


```None
{
    "data": {
        "userUuid": "686391060106801152"
    },
    "code": 200,
    "success": true,
    "requestId": "116e33db-01a8-427e-b021-e9da1901f127"
}
```


### 修改邮箱


```None
curl --location --request POST 'https://api.yingdao.com/oapi/rpa/user/v1/modify' \
--header 'Authorization: Bearer 75b71079-1b81-439b-94b3-294b13b7c478' \
--header 'User-Agent: Apifox/1.0.0 (https://apifox.com)' \
--header 'Content-Type: application/json' \
--data-raw '{
    "account": "test-0514@fckj",
    "email": "test01@yingdao.com"
}'
```

返回值


```None
{
    "data": {
        "userUuid": "686391060106801152"
    },
    "code": 200,
    "success": true,
    "requestId": "c9fc2026-4f71-4a2a-9c7d-f6041d73a3d2"
}
```


### 修改用户角色


```None
curl --location --request POST 'https://api.yingdao.com/oapi/rpa/user/v1/modify' \
--header 'Authorization: Bearer 75b71079-1b81-439b-94b3-294b13b7c478' \
--header 'User-Agent: Apifox/1.0.0 (https://apifox.com)' \
--header 'Content-Type: application/json' \
--data-raw '{
    "account": "test-0514@fckj",
    "userRole": "e_user"
}'
```

返回值


```None
{
    "data": {
        "userUuid": "686391060106801152"
    },
    "code": 200,
    "success": true,
    "requestId": "f9a85b50-b4a4-4393-8858-bf1a58864ac8"
}
```


================================================================================
## 文档路径: 开放API/API接口/RPA企业账号/删除RPA企业账号
================================================================================

# 删除RPA企业账号
路径: 开放API/API接口/RPA企业账号/删除RPA企业账号


# 删除RPA企业账号

该接口用于删除删除RPA企业账号


## 前置操作

1. 使用鉴权接口获取accessToken。

使用鉴权接口获取accessToken。


## 请求

|  |
|  |
| **HTTP URL** | https://api.yingdao.com/oapi/rpa/user/v1/delete |  |
| **HTTP Method** | POST |  |

**基本**

**参数值**

**说明**

**HTTP URL**

https://api.yingdao.com/oapi/rpa/user/v1/delete


**HTTP Method**

POST



### 请求头

|  |
|  |
| **Authorization** | Bearer {accessToken} | {accessToken}变量需要替换成鉴权接口返回的access Token |

**基本**

**参数值**

**说明**

**Authorization**

Bearer {accessToken}

{accessToken}变量需要替换成鉴权接口返回的access Token


### 请求参数

|  |
|  |
| **account** | string | 登录账号, （确定需要修改的账号）, 必传 |
| **receiveAccount** | string | 接收的登录账号（删除account账号时，将其开发的应用转移给receiveAccount账号）, 必传 |

**名称**

**类型**

**说明**

**account**

string

登录账号, （确定需要修改的账号）, 必传

**receiveAccount**

string

接收的登录账号（删除account账号时，将其开发的应用转移给receiveAccount账号）, 必传



## 响应


### 响应数据结构

|  |
|  |
| **code** | integer | 返回结果编码 |
| **success** | boolean | true 表示成功，false 表示失败 |
| **requestId** | string |  |
| **msg** | string | 结果信息 |

**名称**

**类型**

**说明**

**code**

integer

返回结果编码

**success**

boolean

true 表示成功，false 表示失败

**requestId**

string


**msg**

string

结果信息


## 补充说明

- code 说明

code 说明

|  |
|  |
| **200** | 成功 |
| **400** | 缺少参数(参数错误) |
| **20010012** | 用户不存在 |
| **20010028** | 接收用户不存在 |

**code**

**说明**

**200**

成功

**400**

缺少参数(参数错误)

**20010012**

用户不存在

**20010028**

接收用户不存在



## 示例


### 删除账号-有接收账号


```None
curl --location --request POST 'https://api.yingdao.com/oapi/rpa/user/v1/delete' \
--header 'Authorization: Bearer 75b71079-1b81-439b-94b3-294b13b7c478' \
--header 'User-Agent: Apifox/1.0.0 (https://apifox.com)' \
--header 'Content-Type: application/json' \
--data-raw '{
    "account": "test0514-01@fckj",
    "receiveAccount": "test0514-03@fckj"
}'
```

返回值


```None
{
    "code": 200,
    "success": true,
    "requestId": "ec1e1082-74e4-4f32-b4b4-90cfe0da5db8"
}
```


================================================================================
## 文档路径: 开放API/API接口/RPA企业账号/重置账号密码
================================================================================

# 重置账号密码
路径: 开放API/API接口/RPA企业账号/重置账号密码


# 重置账号密码


## 前置操作

1. 使用鉴权接口获取accessToken。

使用鉴权接口获取accessToken。


## 请求

|  |
|  |
| **HTTP URL** | https://api.yingdao.com/oapi/useracl/v1/rest/pwd |  |
| **HTTP Method** | POST |  |

**基本**

**参数值**

**说明**

**HTTP URL**

https://api.yingdao.com/oapi/useracl/v1/rest/pwd


**HTTP Method**

POST



### 请求头

|  |
|  |
| **Authorization** | Bearer {accessToken} | {accessToken}变量需要替换成鉴权接口返回的access Token |

**基本**

**参数值**

**说明**

**Authorization**

Bearer {accessToken}

{accessToken}变量需要替换成鉴权接口返回的access Token


### 请求参数

|  |
|  |
| **loginAccount** | string | 账号, （确定需要修改的账号）, 必传 |
| **oldPwd** | string | 老密码，修改时需要和现有密码进行比对 |
| **pwd** | string | 新密码，不能为空 |

**名称**

**类型**

**说明**

**loginAccount**

string

账号, （确定需要修改的账号）, 必传

**oldPwd**

string

老密码，修改时需要和现有密码进行比对

**pwd**

string

新密码，不能为空



## 响应


### 响应数据结构

|  |
|  |
| **data** | object | 结果数据 |
| **code** | integer | 返回结果编码 200为成功，其他错误结果都为500 |
| **success** | boolean | true 表示成功，false 表示失败 |
| **requestId** | string |  |
| **msg** | string | 结果信息 |

**名称**

**类型**

**说明**

**data**

object

结果数据

**code**

integer

返回结果编码 200为成功，其他错误结果都为500

**success**

boolean

true 表示成功，false 表示失败

**requestId**

string


**msg**

string

结果信息



## 使用示例


### 成功修改密码


```None
curl --location --request POST 'https://test-api.yingdao.com/oapi/useracl/v1/rest/pwd' \
--header 'Authorization: Bearer 2ab12245-769b-4f53-99ce-8bab9329efed' \
--header 'User-Agent: Apifox/1.0.0 (https://apifox.com)' \
--header 'Content-Type: application/json' \
--data-raw '{
    "loginAccount":"七六@fckj",
    "oldPwd":"123456", 
    "pwd":"1234567"
}'
```

返回值


```None
{"data":true,"code":200,"success":true}
```


### 修改密码失败


```None
curl --location --request POST 'https://test-api.yingdao.com/oapi/useracl/v1/rest/pwd' \
--header 'Authorization: Bearer 2ab12245-769b-4f53-99ce-8bab9329efed' \
--header 'User-Agent: Apifox/1.0.0 (https://apifox.com)' \
--header 'Content-Type: application/json' \
--data-raw '{
    "loginAccount":"七六@fckj",
    "oldPwd":"123456", 
    "pwd":"1234567"
}'
```

返回值


```None
{"code":500,"success":false,"msg":"新密码不能与当前密码相同，请输入一个不同的新密码"}
```


================================================================================
## 文档路径: 开放API/API接口/工作队列/重新排队
================================================================================

# 重新排队
路径: 开放API/API接口/工作队列/重新排队


# 重新排队

该接口用于将指定队列的指定队列项**重新排队， 仅支持从****正在处理(processing)状态变更为排队中(queued)状态**。


## 前置操作

1. 使用鉴权接口获取accessToken。

使用鉴权接口获取accessToken。


## 请求

| **基本** | **参数值** | **说明** |
| --- | --- | --- |
| **HTTP URL** | https://api.yingdao.com/oapi/tool/queue/v1/queueitems/{itemUuid}/reenqueue | 将 {{itemUuid}} 替换成对列项UUID |
| **HTTP Method** | PATCH |  |

**基本**

**参数值**

**说明**

**HTTP URL**

https://api.yingdao.com/oapi/tool/queue/v1/queueitems/{itemUuid}/reenqueue

将 {{itemUuid}} 替换成对列项UUID

**HTTP Method**

PATCH



### 请求头

| **基本** | **参数值** | 说明 |
| --- | --- | --- |
| **Authorization** | Bearer {accessToken} | {accessToken}变量需要替换成鉴权接口返回的access Token |
| Accept | */* |  |
| Content-Type | application/json |  |

**基本**

**参数值**

说明

**Authorization**

Bearer {accessToken}

{accessToken}变量需要替换成鉴权接口返回的access Token

Accept

*/*


Content-Type

application/json



### 请求参数

| **名称** | **类型** | **是否必填** | 说明 |
| --- | --- | --- | --- |
| effectiveTime | Long | 是 | 生效时间，值为时间戳，单位：秒 |
| expireTime | Long | 否 | 过期时间，值为时间戳，单位：秒 |
| description | String | 否 | 描述，长度应在0~2000之间 |

**名称**

**类型**

**是否必填**

说明

effectiveTime

Long

是

生效时间，值为时间戳，单位：秒

expireTime

Long

否

过期时间，值为时间戳，单位：秒

description

String

否

描述，长度应在0~2000之间


## 响应


### 响应数据结构

| **名称** | **类型** | **说明** |
| --- | --- | --- |
| **code** | integer | 返回结果编码200表示成功，其他表示失败 |
| **success** | boolean | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **requestId** | string | 请求id，方便排查使用 |
| **serverIp** | string | 处理服务的Ip |
| **serverInstName** | string | 处理服务实例名称 |
| **msg** | string | 状态码描述 |

**名称**

**类型**

**说明**

**code**

integer

返回结果编码200表示成功，其他表示失败

**success**

boolean

调用是否成功，可以根据该字段判断接口调用是否成功

**requestId**

string

请求id，方便排查使用

**serverIp**

string

处理服务的Ip

**serverInstName**

string

处理服务实例名称

**msg**

string

状态码描述


### 响应数据案例


```None
{
  "code": 0,
  "success": false,
  "requestId": "",
  "serverIp": "",
  "serverInstName": "",
  "msg": ""
}
```


## 使用示例


### curl示例


```None
curl --location --request PATCH 'https://api.yingdao.com/oapi/tool/queue/v1/queueitems/{itemUuid}/reenqueue' \
--header 'Authorization: Bearer {accessToken}' \
--header 'Content-Type: application/json' \
--header 'Accept: */*' \ 
--header 'Host: api.yingdao.com' \
--data-raw '{
    "effectiveTime": 0,
    "expireTime": 0,
    "description": "string"
}'
```


## 其他


### 队列项状态

|  |
|  |
| queued | 排队中 |
| processing | 正在处理 |
| processed | 已处理 |
| exception | 异常 |
| on hold | 挂起 |
| expired | 超时过期 |

**status**

**说明**

queued

排队中

processing

正在处理

processed

已处理

exception

异常

on hold

挂起

expired

超时过期


================================================================================
## 文档路径: 开放API/API接口/工作队列/修改队列项
================================================================================

# 修改队列项
路径: 开放API/API接口/工作队列/修改队列项


# 修改队列项

该接口用于修改指定队列的指定队列项状态为**已处理(processed)或异常(exception)**和队列项描述。


## 前置操作

1. 使用鉴权接口获取accessToken。

使用鉴权接口获取accessToken。


## 请求

| **基本** | **参数值** | **说明** |
| --- | --- | --- |
| **HTTP URL** | https://api.yingdao.com/oapi/tool/queue/v1/queueitems/{{itemUuid}} | 将 {{itemUuid}} 替换成对列项UUID |
| **HTTP Method** | PATCH |  |

**基本**

**参数值**

**说明**

**HTTP URL**

https://api.yingdao.com/oapi/tool/queue/v1/queueitems/{{itemUuid}}

将 {{itemUuid}} 替换成对列项UUID

**HTTP Method**

PATCH



### 请求头

| **基本** | **参数值** | **说明** |
| --- | --- | --- |
| **Authorization** | Bearer {accessToken} | {accessToken}变量需要替换成鉴权接口返回的access Token |
| Accept | */* |  |
| Content-Type | application/json |  |

**基本**

**参数值**

**说明**

**Authorization**

Bearer {accessToken}

{accessToken}变量需要替换成鉴权接口返回的access Token

Accept

*/*


Content-Type

application/json



### 请求参数

| **名称** | **类型** | **是否必填** | **说明** |
| --- | --- | --- | --- |
| status | String | 是 | 状态, processed、exception |
| description | String | 否 | 描述，长度应在0~2000之间 |

**名称**

**类型**

**是否必填**

**说明**

status

String

是

状态, processed、exception

description

String

否

描述，长度应在0~2000之间


## 响应


### 响应数据结构

| **名称** | **类型** | **说明** |
| --- | --- | --- |
| **code** | integer | 返回结果编码200表示成功，其他表示失败 |
| **success** | boolean | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **requestId** | string | 请求id，方便排查使用 |
| **serverIp** | string | 处理服务的Ip |
| **serverInstName** | string | 处理服务实例名称 |
| **msg** | string | 状态码描述 |

**名称**

**类型**

**说明**

**code**

integer

返回结果编码200表示成功，其他表示失败

**success**

boolean

调用是否成功，可以根据该字段判断接口调用是否成功

**requestId**

string

请求id，方便排查使用

**serverIp**

string

处理服务的Ip

**serverInstName**

string

处理服务实例名称

**msg**

string

状态码描述


### 响应数据案例


```None
{
  "code": 0,
  "success": false,
  "requestId": "",
  "serverIp": "",
  "serverInstName": "",
  "msg": ""
}
```


## 使用示例


### curl示例


```None
curl --location --request PATCH 'https://api.yingdao.com/oapi/tool/queue/v1/queueitems/{{itemUuid}}' \
--header 'Authorization: Bearer {accessToken}' \
--header 'Content-Type: application/json' \
--header 'Accept: */*' \ 
--header 'Host: api.yingdao.com' \
--data-raw '{
    "status": "processed",
    "description": "string"
}'
```


## 其他


### 队列项状态

|  |
|  |
| queued | 排队中 |
| processing | 正在处理 |
| processed | 已处理 |
| exception | 异常 |
| on hold | 挂起 |
| expired | 超时过期 |

**status**

**说明**

queued

排队中

processing

正在处理

processed

已处理

exception

异常

on hold

挂起

expired

超时过期


================================================================================
## 文档路径: 开放API/API接口/工作队列/出列
================================================================================

# 出列
路径: 开放API/API接口/工作队列/出列


# 出列

该接口用于从指定的队列获取一个**排队中**的队列项，并将队列项状态修改为**正在处理**。


## 前置操作

1. 使用鉴权接口获取accessToken。

使用鉴权接口获取accessToken。


## 请求

| **基本** | **参数值** | **说明** |
| --- | --- | --- |
| **HTTP URL** | https://api.yingdao.com/oapi/tool/queue/v1/queues/{{queueUuid}}/dequeue | 将 {{queueUuid}} 替换队列UUID |
| **HTTP Method** | PATCH |  |

**基本**

**参数值**

**说明**

**HTTP URL**

https://api.yingdao.com/oapi/tool/queue/v1/queues/{{queueUuid}}/dequeue

将 {{queueUuid}} 替换队列UUID

**HTTP Method**

PATCH



### 请求头

| **基本** | **参数值** | **说明** |
| --- | --- | --- |
| **Authorization** | Bearer {accessToken} | {accessToken}变量需要替换成鉴权接口返回的access Token |
| Accept | */* |  |

**基本**

**参数值**

**说明**

**Authorization**

Bearer {accessToken}

{accessToken}变量需要替换成鉴权接口返回的access Token

Accept

*/*



### 请求参数

无


## 响应


### 响应数据结构

| **名称** | **类型** | **说明** |
| --- | --- | --- |
| **data** | object | 结果数据 |
| **|─uuid** | string | 队列项uuid |
| **|─name** | string | 名称 |
| **|─status** | string | 状态 |
| **|─priority** | Integer | 优先级 |
| **|─expireTime** | Long | 到期时间，值为时间戳，单位：秒 |
| **|─bizInfo** | string | 业务信息 |
| **|─description** | string | 描述 |
| **|─createTime** | Long | 创建时间，值为时间戳，单位：秒 |
| **|─updateTime** | Long | 更新时间，值为时间戳，单位：秒 |
| **code** | integer | 返回结果编码200表示成功，其他表示失败 |
| **success** | boolean | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **requestId** | string | 请求id，方便排查使用 |
| **serverIp** | string | 处理服务的Ip |
| **serverInstName** | string | 处理服务实例名称 |
| **msg** | string | 状态码描述 |

**名称**

**类型**

**说明**

**data**

object

结果数据

**|─uuid**

string

队列项uuid

**|─name**

string

名称

**|─status**

string

状态

**|─priority**

Integer

优先级

**|─expireTime**

Long

到期时间，值为时间戳，单位：秒

**|─bizInfo**

string

业务信息

**|─description**

string

描述

**|─createTime**

Long

创建时间，值为时间戳，单位：秒

**|─updateTime**

Long

更新时间，值为时间戳，单位：秒

**code**

integer

返回结果编码200表示成功，其他表示失败

**success**

boolean

调用是否成功，可以根据该字段判断接口调用是否成功

**requestId**

string

请求id，方便排查使用

**serverIp**

string

处理服务的Ip

**serverInstName**

string

处理服务实例名称

**msg**

string

状态码描述


### 响应数据案例


```None
{
        "data": {
                "uuid": "string",
                "name": "string",
                "status": "string",
                "priority": 0,
                "expireTime": 0,
                "bizInfo": "string",
                "description": "string",
                "createTime": 0,
                "updateTime": 0
        },
        "code": 0,
        "success": true,
        "requestId": "string",
        "serverIp": "string",
        "serverInstName": "string",
        "msg": "string"
}
```


## 使用示例


### curl示例


```None
curl --location --request PATCH 'https://api.yingdao.com/oapi/tool/queue/v1/queues/{{queueUuid}}/dequeue' \
--header 'Authorization: Bearer {accessToken}' \
--header 'Content-Type: application/json' \
--header 'Accept: */*' \ 
--header 'Host: api.yingdao.com' 
```


## 其他


### 队列项状态

|  |
|  |
| queued | 排队中 |
| processing | 正在处理 |
| processed | 已处理 |
| exception | 异常 |
| on hold | 挂起 |
| expired | 超时过期 |

**status**

**说明**

queued

排队中

processing

正在处理

processed

已处理

exception

异常

on hold

挂起

expired

超时过期


================================================================================
## 文档路径: 开放API/API接口/工作队列/新增队列项
================================================================================

# 新增队列项
路径: 开放API/API接口/工作队列/新增队列项


# 新增队列项

该接口用于向指定的队列新增队列项。


## 前置操作

1. 使用鉴权接口获取accessToken。

使用鉴权接口获取accessToken。


## 请求

|  |
|  |
| **HTTP URL** | https://api.yingdao.com/oapi/tool/queue/v1/queues/{{queueUuid}}/enqueue | 队列UUID参数需做替换，专有云企业请使用专有云地址 |
| **HTTP Method** | POST |  |

**基本**

**参数值**

**说明**

**HTTP URL**

https://api.yingdao.com/oapi/tool/queue/v1/queues/{{queueUuid}}/enqueue

队列UUID参数需做替换，专有云企业请使用专有云地址

**HTTP Method**

POST



### 请求头

|  |
|  |
| **Authorization** | Bearer {accessToken} | {accessToken}变量需要替换成鉴权接口返回的access Token |

**基本**

**参数值**

**说明**

**Authorization**

Bearer {accessToken}

{accessToken}变量需要替换成鉴权接口返回的access Token


### 请求参数

|  |
|  |
| **name** | String | 是 | 队列任务项名称，长度应在1~100之间 |
| **priority** | Integer | 是 | 优先级，枚举值 【0，100，200】0-高，100-中，200-低 |
| **expireTime** | Long | 否 | 过期时间，值为时间戳，单位：秒 |
| **effectiveTime** | Long | 否 | 生效时间，值为时间戳，单位：秒 |
| **bizInfo** | String | 是 | 业务信息，长度应在1~1000之间 |
| **description** | String | 否 | 描述，长度应在0~2000之间 |
| **source** | String | 是 | 来源，使用 OpenAPI 即可 |

**名称**

**类型**

**是否必填**

**说明**

**name**

String

是

队列任务项名称，长度应在1~100之间

**priority**

Integer

是

优先级，枚举值 【0，100，200】0-高，100-中，200-低

**expireTime**

Long

否

过期时间，值为时间戳，单位：秒

**effectiveTime**

Long

否

生效时间，值为时间戳，单位：秒

**bizInfo**

String

是

业务信息，长度应在1~1000之间

**description**

String

否

描述，长度应在0~2000之间

**source**

String

是

来源，使用 OpenAPI 即可



## 响应


### 响应数据结构

|  |
|  |
| **data** | object | 结果数据 |
| **    |─uuid** | string | 队列项uuid |
| **    |─name** | string | 名称 |
| **    |─status** | string | 状态 |
| **    |─priority** | Integer | 优先级 |
| **    |─expireTime** | Long | 到期时间，值为时间戳，单位：秒 |
| **    |─bizInfo** | string | 业务信息 |
| **    |─description** | string | 描述 |
| **    |─createTime** | Long | 创建时间，值为时间戳，单位：秒 |
| **    |─updateTime** | Long | 更新时间，值为时间戳，单位：秒 |
| **code** | integer | 返回结果编码200表示成功，其他表示失败 |
| **success** | boolean | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **requestId** | string | 请求id，方便排查使用 |
| **serverIp** | string | 处理服务的Ip |
| **serverInstName** | string | 处理服务实例名称 |
| **msg** | string | 状态码描述 |

**名称**

**类型**

**说明**

**data**

object

结果数据

**    |─uuid**

string

队列项uuid

**    |─name**

string

名称

**    |─status**

string

状态

**    |─priority**

Integer

优先级

**    |─expireTime**

Long

到期时间，值为时间戳，单位：秒

**    |─bizInfo**

string

业务信息

**    |─description**

string

描述

**    |─createTime**

Long

创建时间，值为时间戳，单位：秒

**    |─updateTime**

Long

更新时间，值为时间戳，单位：秒

**code**

integer

返回结果编码200表示成功，其他表示失败

**success**

boolean

调用是否成功，可以根据该字段判断接口调用是否成功

**requestId**

string

请求id，方便排查使用

**serverIp**

string

处理服务的Ip

**serverInstName**

string

处理服务实例名称

**msg**

string

状态码描述



### 响应数据案例


```None
{
	"data": {
		"uuid": "q683587567319805952_R683587960149929985",
		"name": "1",
		"status": "queued",
		"priority": 100,
		"expireTime": 1714993713,
		"bizInfo": "1",
		"description": "1",
		"createTime": 1714983758,
		"updateTime": 1714983758
	},
	"page": null,
	"code": 200,
	"success": true,
	"requestId": null,
	"serverIp": null,
	"serverInstName": null,
	"msg": null
}
```


## 使用示例


### curl示例


```None
curl --location --request POST 'https://api.yingdao.com/oapi/tool/queue/v1/queues/{queueUuid}/enqueue' \ --header 'Authorization: Bearer 2eed910f-6ade-4e0c-9007-0feade4f5df6' \ \ --header 'Content-Type: application/json' \ --header 'Accept: */*' \ --header 'Host: api.yingdao.com' \ --header 'Connection: keep-alive' \ --data-raw '{ "name": "1", "priority": 100, "expireTime": 1714993713, "bizInfo": "1", "description": "1", "source": "OpenAPI" }'
```


================================================================================
## 文档路径: 开放API/API接口/任务运行/启动任务API
================================================================================

# 启动任务API
路径: 开放API/API接口/任务运行/启动任务API


# 启动任务


## 前置操作

需要使用鉴权接口获取accessToken后，填写到对应的hearder中

**说明：**该接口需要在控制台新建任务，编排好应用和机器人，适用于更复杂的调度场景，该接口也支持从外部传入应用运行参数，如果有多个应用，需要填写多个应用的运行参数，服务内部逻辑会根据robotUuid取出传入的应用运行参数，透传到客户端


### 重点说明

影刀针对输入参数会有大小限制，一般建议所有输入参数加起来不超过8000，如遇到输入参数超过阈值，可有两种方案解决

**方案一:** 可以进行输入参数切割，如电商场景，1000个订单号传进来一次性调用，可以切割成100个订单号进行一次调用，将一次请求转换成10次

**方案二: **可以把长文本转换成文件类型传递

步骤一:打开客户端，修改RPA流程，将字符串类型参数改成文件路径参数类型

步骤二 :保存并发版应用

步骤三:将文本参数转成文件上传到影刀文件服务器(文件上传)，返回文件key值

步骤三:api调用时，参数类型(type)修改成file类型，传入步骤三获取的文件key值


## 模板


### postMan模板

api启动任务.json（右键另存为）


### Java模板

请求模型：TaskStartReq.java（右键另存为）

响应模型：TaskStartRep.java（右键另存为）空

应用关联的运行参数模型:RobotRelaParam.java（右键另存为）


## 请求

|  |
|  |
| **HTTP URL** | https://api.yingdao.com/oapi/dispatch/v2/task/start | 专有云企业请使用专有云地址 |
| **HTTP Method** | POST |  |

**基本**

**参数值**

**说明**

**HTTP URL**

https://api.yingdao.com/oapi/dispatch/v2/task/start

专有云企业请使用专有云地址

**HTTP Method**

POST



### 请求头

|  |
|  |
| **Authorization** | Bearer {accessToken} | accessToken变量需要替换成鉴权接口返回的accessToken |
| **Content-Type** | application/json |  |

**基本**

**参数值**

**说明**

**Authorization**

Bearer {accessToken}

accessToken变量需要替换成鉴权接口返回的accessToken

**Content-Type**

application/json



### 请求体

|  |
|  |
| **scheduleUuid** | string | 任务uuid | 是 | 启动任务uuid，可在控制台-任务管理-右键获取 |
| **idempotentUuid** | string | 幂等uuid | 否 | 本次请求幂等uuid，建议使用uuid，避免网络重试多次触发任务执行, 影刀检测到两次请求的幂等id一样，会执行一次，并且第二次会返回上一次的taskUuid |
| **scheduleRelaParams** | array | 任务运行关联的应用参数 | 否 | 任务可能配置多个应用运行参数，是一个数组结构，需要指定robotUuid和对应的应用参数，如果没有指定，会取默认的应用运行参数 |
| **  ∟ robotUuid** | string | 应用uuid | 否 | 带运行参数的应用uuid |
| **  ∟ runTimeout** | number | 应用运行超时 | 否 | 可用于指定应用运行多长时间后自动停止， 常用来避免应用运行时间不可控或者卡死，影响排队任务运行，最小设置60 最大设置950400，单位秒，需要配合客户端5.10以及之上版本使用 |
| **  ∟ params** | array | 应用运行参数 | 否 | 关联该应用的应用运行参数,最大不能超过8000长度 |
| **    ∟ name** | string | 参数名称 | 否 | 参数名称 |
| **    ∟ value** | string | 参数值 | 否 | 参数值 如果是文件类型可以使用文件上传接口文件上传接口先上传文件，将响应的fileKey作为参数值传递 |
| **    ∟ type** | string | 参数类型 | 否 | 参数类型，参考：应用运行参数枚举值说明 |

**名称**

**类型**

**说明**

**是否必填**

**描述**

**scheduleUuid**

string

任务uuid

是

启动任务uuid，可在控制台-任务管理-右键获取

**idempotentUuid**

string

幂等uuid

否

本次请求幂等uuid，建议使用uuid，避免网络重试多次触发任务执行, 影刀检测到两次请求的幂等id一样，会执行一次，并且第二次会返回上一次的taskUuid

**scheduleRelaParams**

array

任务运行关联的应用参数

否

任务可能配置多个应用运行参数，是一个数组结构，需要指定robotUuid和对应的应用参数，如果没有指定，会取默认的应用运行参数

**  ∟ robotUuid**

string

应用uuid

否

带运行参数的应用uuid

**  ∟ runTimeout**

number

应用运行超时

否

可用于指定应用运行多长时间后自动停止， 常用来避免应用运行时间不可控或者卡死，影响排队任务运行，最小设置60 最大设置950400，单位秒，需要配合客户端5.10以及之上版本使用

**  ∟ params**

array

应用运行参数

否

关联该应用的应用运行参数,最大不能超过8000长度

**    ∟ name**

string

参数名称

否

参数名称

**    ∟ value**

string

参数值

否

参数值 如果是文件类型可以使用文件上传接口文件上传接口先上传文件，将响应的fileKey作为参数值传递

**    ∟ type**

string

参数类型

否

参数类型，参考：应用运行参数枚举值说明

**指定运行参数时，必须需要指定robotUuid**


#### 请求示例


```None
// api调用任务
{
  "scheduleUuid": "79985266-f37a-4bb1-b456-b928914d3437",
  "idempotentUuid":"adss82cb-3333-111-1112-asdsad",
  "scheduleRelaParams": [
    {
      "robotUuid": "8ccc82cb-3945-4c7e-bb02-ab4ba4b183fd",
      "runTimeout":456,
      "params": [
        {
          "name": "str1",
          "value": "测试1",
          "type": "str"
        }
      ]
    }
  ]
}
```



## 响应


### 响应体

|  |
|  |
| **code** | int | 是 | 状态码 200表示成功，非200表示失败 参考：状态码说明 |
| **success** | boolean | 是 | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **msg** | string | 是 | 状态码描述 |
| **data** | object | 是 | 响应数据 |
| **  ∟ taskUuid** | string | 是 | 任务运行uuid |
| **  ∟ jobUuidList** | array | 是 | 任务下每条应用的运行记录uuid集合 |
| **  ∟ idempotentFlag** | boolean | 是 | 是否幂等创建标识，为true时表示重复创建，配合入参idempotentUuid使用 |

**名称**

**类型**

**是否必填**

**描述**

**code**

int

是

状态码 200表示成功，非200表示失败 参考：状态码说明

**success**

boolean

是

调用是否成功，可以根据该字段判断接口调用是否成功

**msg**

string

是

状态码描述

**data**

object

是

响应数据

**  ∟ taskUuid**

string

是

任务运行uuid

**  ∟ jobUuidList**

array

是

任务下每条应用的运行记录uuid集合

**  ∟ idempotentFlag**

boolean

是

是否幂等创建标识，为true时表示重复创建，配合入参idempotentUuid使用



#### 响应体示例


```None
{
    "data": {
        "taskUuid": "fc38fbsa-8333-1111-83f8-3292aaaaaa",
        "jobUuidList": ["fd57564f-11f5-4035-a20f-b2838fcc0b05", "d64b1246-f0ef-436e-a948-01c325614e16"],
        "idempotentFlag": false
    },
    "code": 200,
    "success": true
}
```

如遇到错误，请跳转到 状态码说明


================================================================================
## 文档路径: 开放API/API接口/任务运行/查询任务运行结果API
================================================================================

# 查询任务运行结果API
路径: 开放API/API接口/任务运行/查询任务运行结果API


# 查询任务运行结果


## 前置操作

需要先调用启动任务接口，获取taskUuid

说明：该接口是可以轮询任务运行结果，可获取任务下多个应用的运行结果数据，当任务运行结果处于终态时，需要停止轮询，任务运行状态参考 任务运行状态枚举说明


## 模板


### postMan模板

api查询任务运行结果.json（右键另存为）


### Java模板

请求模型：TaskQueryReq.java（右键另存为）

响应模型：TaskQueryRep.java（右键另存为）

应用运行结果模型:JobQueryRep.java（右键另存为）


## 请求

|  |
|  |
| **HTTP URL** | https://api.yingdao.com/oapi/dispatch/v2/task/query | 专有云企业请使用专有云地址 |
| **HTTP Method** | POST |  |

**基本**

**参数值**

**说明**

**HTTP URL**

https://api.yingdao.com/oapi/dispatch/v2/task/query

专有云企业请使用专有云地址

**HTTP Method**

POST



### 请求头

|  |
|  |
| **Authorization** | Bearer {accessToken} | {accessToken}变量需要替换成鉴权接口返回的accessToken |
| **Content-Type** | application/json |  |

**基本**

**参数值**

**说明**

**Authorization**

Bearer {accessToken}

{accessToken}变量需要替换成鉴权接口返回的accessToken

**Content-Type**

application/json



### 请求体

|  |
|  |
| **taskUuid** | string | 任务运行uuid | 是 | 由启动任务接口返 |

**名称**

**类型**

**说明**

**是否必填**

**描述**

**taskUuid**

string

任务运行uuid

是

由启动任务接口返


### 请求示例


```None
{
  "taskUuid":"4d8aae66-cec5-4043-85cc-70f4e0430111"
}
```



## 响应


### 响应体

|  |
|  |
| **code** | int | 是 | 状态码 200表示成功，非200表示失败 参考：状态码说明 |
| **success** | boolean | 是 | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **msg** | string | 是 | 状态码描述 |
| **data** | object | 是 | 响应数据 |
| **∟ taskUuid** | string | 是 | 任务运行uuid |
| **∟ taskName** | string | 是 | 任务名称 |
| **∟ status** | string | 是 | 任务运行状态，该字段可以判断任务是否终态，终态时需要停止轮询该接口，参考：任务运行状态枚举说明 |
| **∟ statusName** | string | 是 | 任务运行状态描述 |
| **∟ startTime** | string | 否 | 任务运行开始时间，当任务被调度时不为空 |
| **∟ endTime** | string | 否 | 任务运行结束时间，当任务结束运行时不为空 |
| **∟ jobDataList** | array | 是 | 任务所关联的应用运行信息，多个应用有多条 |
| **∟ jobUuid** | string | 是 | 应用运行uuid |
| **∟ status** | string | 是 | 应用运行状态 |
| **∟ statusName** | string | 是 | 应用运行状态描述 |
| **∟ remark** | string | 否 | 备注信息，当运行异常，值不为空 |
| **∟ robotClientUuid** | string | 否 | 机器人uuid，当应用已被调度之后，值不为空 |
| **∟ robotClientName** | string | 否 | 机器人名称，当应用已被调度之后，值不为空 |
| **∟ startTime** | string | 否 | 应用开始运行时间，当应用开始调度之后，值不为空 |
| **∟ endTime** | string | 否 | 应用结束运行时间，当应用结束调度之后，值不为空 |
| **∟ robotUuid** | string | 是 | 应用uuid |
| **∟ robotName** | string | 是 | 应用名称 |
| **∟ screenshotUrl** | string | 否 | job的截屏url |
| **∟ robotParams** | object | 否 | 应用运行参数 |
| **∟ inputs** | array | 否 | 输入参数 |
| **∟ name** | string | 否 | 参数名称 |
| **∟ value** | string | 否 | 参数值 |
| **∟ type** | string | 否 | 参数类型，参考：任务运行状态枚举说明 |

**名称**

**类型**

**是否必填**

**描述**

**code**

int

是

状态码 200表示成功，非200表示失败 参考：状态码说明

**success**

boolean

是

调用是否成功，可以根据该字段判断接口调用是否成功

**msg**

string

是

状态码描述

**data**

object

是

响应数据

**∟ taskUuid**

string

是

任务运行uuid

**∟ taskName**

string

是

任务名称

**∟ status**

string

是

任务运行状态，该字段可以判断任务是否终态，终态时需要停止轮询该接口，参考：任务运行状态枚举说明

**∟ statusName**

string

是

任务运行状态描述

**∟ startTime**

string

否

任务运行开始时间，当任务被调度时不为空

**∟ endTime**

string

否

任务运行结束时间，当任务结束运行时不为空

**∟ jobDataList**

array

是

任务所关联的应用运行信息，多个应用有多条

**∟ jobUuid**

string

是

应用运行uuid

**∟ status**

string

是

应用运行状态

**∟ statusName**

string

是

应用运行状态描述

**∟ remark**

string

否

备注信息，当运行异常，值不为空

**∟ robotClientUuid**

string

否

机器人uuid，当应用已被调度之后，值不为空

**∟ robotClientName**

string

否

机器人名称，当应用已被调度之后，值不为空

**∟ startTime**

string

否

应用开始运行时间，当应用开始调度之后，值不为空

**∟ endTime**

string

否

应用结束运行时间，当应用结束调度之后，值不为空

**∟ robotUuid**

string

是

应用uuid

**∟ robotName**

string

是

应用名称

**∟ screenshotUrl**

string

否

job的截屏url

**∟ robotParams**

object

否

应用运行参数

**∟ inputs**

array

否

输入参数

**∟ name**

string

否

参数名称

**∟ value**

string

否

参数值

**∟ type**

string

否

参数类型，参考：任务运行状态枚举说明



#### 响应体示例

status可用于停止轮询的标识，当状态终态时，需要停止轮询，参考：任务运行状态枚举说明


##### 任务运行有主流程输入，输出参数


```None
{
    "data": {
        "taskUuid": "4d8aae66-cec5-4043-85cc-70f4e0430d4e",
        "taskName": "测试-api任务", 
        "status": "running",  
        "statusName": "运行中",
        "startTime": "2022-01-22 15:10:28",
        "endTime": "2022-01-22 15:10:46",
        "jobDataList": [
            {
                "jobUuid": "b934597c-f06d-4c52-9624-e62e7f7b9489",
                "status": "finish", 
                "statusName": "完成", 
                "remark": "", 
                "robotParams":  {
            				"name":"获取页数", 
            				"value":"10",
            				"type":"str" 
       					 }, 
                "robotClientUuid": "cfcc5904-2e82-4295-911c-0ce65c9099f2", 
                "robotClientName": "ceshi1@csqy1",
                "robotUuid": "3f3c9861-9300-4400-9c1f-f4e7f8bb4d08", 
                "robotName": "wait-10", 
                "startTime": "2022-01-22 15:10:28", 
                "endTime": "2022-01-22 15:10:46",
                "screenshotUrl": "https://winrobot-pub-a-dev.oss-cn-hangzhou.aliyuncs.com/image/xxx.jpg"
            },
            {
                "jobUuid": "97421b0b-2f64-4adf-94b9-0bdfc73face6",
                "status": "created",
                "statusName": "已创建",
                "remark": "",
                "robotParams":  {
            				"name":"获取页数", 
            				"value":"10",
            				"type":"str" 
       					 }, 
                "robotClientUuid": "cfcc5904-2e82-4295-911c-0ce65c9099f2",
                "robotClientName": "ceshi1@csqy1",
                "robotUuid": "e8be5a0a-ec3a-4f3a-b4a2-b9319fe6fd0a",
                "robotName": "等待-10s",
               "startTime": "2022-01-22 15:10:28", 
                "endTime": "2022-01-22 15:10:46",
                "screenshotUrl": "https://winrobot-pub-a-dev.oss-cn-hangzhou.aliyuncs.com/image/xxx.jpg"
            }
        ]
    },
    "code": 200,
    "success": true
}
```


##### job运行无主流程输入，输出参数


```None
{
    "data": {
        "taskUuid": "4d8aae66-cec5-4043-85cc-70f4e0430d4e",
        "taskName": "测试-api任务",
        "status": "running",
        "statusName": "运行中", 
        "startTime": "2022-01-22 15:10:28", 
        "endTime": "2022-01-22 15:10:46", 
        "jobDataList": [ 
            {
                "jobUuid": "97421b0b-2f64-4adf-94b9-0bdfc73face6",
                "status": "created",
                "statusName": "已创建",
                "robotParams": {},
                "robotClientUuid": "cfcc5904-2e82-4295-911c-0ce65c9099f2",
                "robotClientName": "ceshi1@csqy1", 
                "robotUuid": "e8be5a0a-ec3a-4f3a-b4a2-b9319fe6fd0a",
                "robotName": "等待-10s",
                "startTime": "2022-01-22 15:10:28", 
                "endTime": "2022-01-22 15:10:46",
                "remark": "任务创建"
            }
        ]
    },
    "code": 200,
    "success": true
}
```

如遇到错误，请跳转到 状态码说明


================================================================================
## 文档路径: 开放API/API接口/任务运行/停止任务运行API
================================================================================

# 停止任务运行API
路径: 开放API/API接口/任务运行/停止任务运行API


# 停止任务运行


## 前置操作

需要先调用启动任务接口，获取taskUuid

说明：任务运行状态处于终态，调用该接口无效果


## 模板


### postMan模板

api停止任务运行.json（右键另存为）


### Java模板

请求模型：StopTaskReq.java（右键另存为）

响应模型：无


## 请求

|  |
|  |
| **HTTP URL** | https://api.yingdao.com/oapi/dispatch/v2/task/stop | 专有云企业请使用专有云地址 |
| **HTTP Method** | POST |  |

**基本**

**参数值**

**说明**

**HTTP URL**

https://api.yingdao.com/oapi/dispatch/v2/task/stop

专有云企业请使用专有云地址

**HTTP Method**

POST



## 请求头

|  |
|  |
| **Authorization** | Bearer {accessToken} | {accessToken}变量需要替换成鉴权接口返回的access Token |
| **Content-Type** | application/json |  |

**基本**

**参数值**

**说明**

**Authorization**

Bearer {accessToken}

{accessToken}变量需要替换成鉴权接口返回的access Token

**Content-Type**

application/json



### 请求体

|  |
|  |
| **taskUuid** | string | 任务运行uuid | 是 | 无 |

**名称**

**类型**

**说明**

**是否必填**

**描述**

**taskUuid**

string

任务运行uuid

是

无


### 请求示例


```None
{
  "taskUuid": "45c882ed-e44f-4818-afc0-05172e7ff111"
}
```



## 响应


### 响应体

|  |
|  |
| **code** | int | 是 | 状态码 200表示成功，非200表示失败 参考： 状态码说明 |
| **success** | boolean | 是 | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **msg** | string | 是 | 状态码描述 |

**名称**

**类型**

**是否必填**

**描述**

**code**

int

是

状态码 200表示成功，非200表示失败 参考： 状态码说明

**success**

boolean

是

调用是否成功，可以根据该字段判断接口调用是否成功

**msg**

string

是

状态码描述



### 响应体示例


```None
{
    "code": 200,
    "success": true
}
```

如遇到错误，请跳转到 状态码说明


================================================================================
## 文档路径: 开放API/API接口/任务运行/任务运行回调
================================================================================

# 任务运行回调
路径: 开放API/API接口/任务运行/任务运行回调


# 任务运行回调


## 前置操作

1.使用管理员账号，在影刀控制台登录，在api配置界面配置回调接口；

2.确保接口是可以正常使用；

3.查看己方服务器环境，如果有防火墙，需要联系技术支持把影刀线上ip加入到白名单中；

4.需要先调用启动任务接口，获取jobUuid。

**说明1：应用运行状态处于终态(正常结束，异常结束，已停止)，影刀服务会主动通过己方配置的回调接口回传job运行结果数据，推荐使用回调方式获取数据结果，保证数据及时获取到**；

**说明2：当己方回调接口返回2xx状态码时，影刀会任务对方正常接受并处理数据，不会进行定时补偿，当己方返回非2xx状态码时，影刀会每整点定时补偿一次，直到成功或者24次后，结束掉定时补偿，如果碰到任务状态超过24小时都没收到回调，建议调用**查询任务运行结果。


## 回调对接策略

1. 影刀自身有回调重试功能，当应用运行结束，回调失败后，会连续重试3次，影刀会在24小时内，每小时进行重试，直到重试成功，所以需要对接方在业务层面保障幂等(可以根据taskUuid进行幂等保障)；
2. 影刀建议回调接口采用异步方式，先接受到影刀的回调数据后，返回成功，再进行异步处理(提交到线程池中进行异步处理)；
3. 如果为了保障回调数据必达，建议使用回调 + 轮询的方式结合使用，发起task/start后，建议轮询时间30s间隔轮询一次(半小时30s轮询一次，1小时轮1分钟一次，依次类推)，回调成功后停止轮询。

影刀自身有回调重试功能，当应用运行结束，回调失败后，会连续重试3次，影刀会在24小时内，每小时进行重试，直到重试成功，所以需要对接方在业务层面保障幂等(可以根据taskUuid进行幂等保障)；

影刀建议回调接口采用异步方式，先接受到影刀的回调数据后，返回成功，再进行异步处理(提交到线程池中进行异步处理)；

如果为了保障回调数据必达，建议使用回调 + 轮询的方式结合使用，发起task/start后，建议轮询时间30s间隔轮询一次(半小时30s轮询一次，1小时轮1分钟一次，依次类推)，回调成功后停止轮询。



### 最佳实践

1. startTask成功后，根据taskUuid记录到业务表a(具体命名由对接方定义)中，业务表需要增加taskUuid的唯一索引,业务表a至少包含taskUuid(幂等字段) , 运行状态终态(参考：任务运行状态枚举说明), 失效时间(到期了默认成功，不进行轮询，失效时间建议task/start之后的25小时)；
2. 定时任务轮询该表，按照以上的间隔时间进行轮询，运行状态处于终态或者已经过了25小时后，建议不再轮询；
3. 回调或轮询接受到任务运行状态处于终态(参考：任务运行状态枚举说明)，更新业务表a状态为回调成功；
4. 轮询查询机器人信息接口(视机器人运行应用的时长，建议30s轮询一次)，当机器人状态处于空闲之后，可进行task/start接口调用, 如果您的机器人较多，建议不要同一时间轮询所有机器人，建议错开轮询。

startTask成功后，根据taskUuid记录到业务表a(具体命名由对接方定义)中，业务表需要增加taskUuid的唯一索引,业务表a至少包含taskUuid(幂等字段) , 运行状态终态(参考：任务运行状态枚举说明), 失效时间(到期了默认成功，不进行轮询，失效时间建议task/start之后的25小时)；

定时任务轮询该表，按照以上的间隔时间进行轮询，运行状态处于终态或者已经过了25小时后，建议不再轮询；

回调或轮询接受到任务运行状态处于终态(参考：任务运行状态枚举说明)，更新业务表a状态为回调成功；

轮询查询机器人信息接口(视机器人运行应用的时长，建议30s轮询一次)，当机器人状态处于空闲之后，可进行task/start接口调用, 如果您的机器人较多，建议不要同一时间轮询所有机器人，建议错开轮询。

示例: 如果有100台机器人，建议分为100次进行轮询，每个机器人和每个机器人之间1s间隔之后发起轮询，进行错峰


## **模板**


### **postMan模板**

api任务运行回调模拟接口.json（右键另存为）


### **Java模板**

回调mock接口:FakeCallbackController.java（右键另存为）

回调数据模型：DataTypeResult.java（右键另存为）TaskResult.java（右键另存为）JobResult.java（右键另存为）

应用运行参数模型:RobotParam.java（右键另存为）

枚举:DataTypeEnum.java（右键另存为）JobStatusEnum.java（右键另存为）TaskStatusEnum.java（右键另存为）


## **请求**

无


### 请求头

|  |
|  |
| **Content-Type** | application/json |  |

**基本**

**参数值**

**说明**

**Content-Type**

application/json



### 请求体

|  |
|  |
| **taskUuid** | **string** | 是 | 应用运行uuid |
| **dataType** | **string** | 是 | 回调类型，调用方需要根据该字段，来解析不同回调类型的数据如:当dataType等于job时，表明是job/start接口触发回调，当dataType等于task时，表明是task/start接口触发回调，参考回调数据类型枚举值说明 |
| **startTime** | **date** | 是 | 第一个应用开始运行时间 |
| **endTime** | **date** | 是 | 最后一个应用结束运行时间 |
| **msg** | **string** | 是 | 任务运行备注 |
| **status** | **string** | 是 | 任务运行状态 |
| **idempotentUuid** | **string** | 是 | 本次请求幂等uuid，如果没传随机生成 |
| **jobList** | array |  | 任务下每个应用的运行列表 |
| **∟** **jobUuid** | **string** | 是 | 应用运行uuid |
| **∟** **dataType** | **string** | 是 | 回调类型，调用方需要根据该字段，来解析不同回调类型的数据如:当dataType等于job时，表明是job/start接口触发回调，当dataType等于task时，表明是task/start接口触发回调，参考回调数据类型枚举值说明 |
| **∟ status** | **string** | 是 | 应用运行状态参考 应用运行状态枚举值说明 |
| **∟ screenshotUrl** | **string** | 否 | 异常截屏，状态为error时才有 |
| **∟** **msg** | **string** | 否 | 应用运行信息，当应用运行异常时不为空 |
| **∟ startTime** | **string** | 是 | 应用运行开始时间 |
| **∟ endTime** | **string** | 是 | 应用运行结束时间 |
| **∟ robotClientUuid** | **string** | 是 | 机器人uuid |
| **∟ robotClientName** | **string** | 是 | 机器人名称 |
| **∟ robotName** | **string** | 是 | 应用名称 |
| **∟** **result** | **array** | 否 | 应用运行输出参数 |
| **∟ name** | **string** | 否 | 参数名称 |
| **∟ value** | **string** | 否 | 参数值 |
| **∟ type** | **string** | 否 | 参数类型，参考应用运行参数枚举值说明 |

**名称**

**类型**

**是否必填**

**描述**

**taskUuid**

**string**

是

应用运行uuid

**dataType**

**string**

是

回调类型，调用方需要根据该字段，来解析不同回调类型的数据如:当dataType等于job时，表明是job/start接口触发回调，当dataType等于task时，表明是task/start接口触发回调，参考回调数据类型枚举值说明

**startTime**

**date**

是

第一个应用开始运行时间

**endTime**

**date**

是

最后一个应用结束运行时间

**msg**

**string**

是

任务运行备注

**status**

**string**

是

任务运行状态

**idempotentUuid**

**string**

是

本次请求幂等uuid，如果没传随机生成

**jobList**

array


任务下每个应用的运行列表

**∟** **jobUuid**

**string**

是

应用运行uuid

**∟** **dataType**

**string**

是

回调类型，调用方需要根据该字段，来解析不同回调类型的数据如:当dataType等于job时，表明是job/start接口触发回调，当dataType等于task时，表明是task/start接口触发回调，参考回调数据类型枚举值说明

**∟ status**

**string**

是

应用运行状态参考 应用运行状态枚举值说明

**∟ screenshotUrl**

**string**

否

异常截屏，状态为error时才有

**∟** **msg**

**string**

否

应用运行信息，当应用运行异常时不为空

**∟ startTime**

**string**

是

应用运行开始时间

**∟ endTime**

**string**

是

应用运行结束时间

**∟ robotClientUuid**

**string**

是

机器人uuid

**∟ robotClientName**

**string**

是

机器人名称

**∟ robotName**

**string**

是

应用名称

**∟** **result**

**array**

否

应用运行输出参数

**∟ name**

**string**

否

参数名称

**∟ value**

**string**

否

参数值

**∟ type**

**string**

否

参数类型，参考应用运行参数枚举值说明



#### 回调示例


```None
{
  "dataType": "task", //数据类型 job表示应用运行回调(通过api调用应用robotUuid的方式), task表示任务运行回调(通过api调用任务scheduleUuid的方式)
  "startTime": 1,//可为空 第一个应用开始运行时间
  "endTime": 1642837962000, //最后一个应用结束运行时间
  "jobList": [
    {
      "dataType": "job",
      "jobUuid": "6de893bb-8224-4f60-9bff-b8597b8ed8fc",
      "msg": "",
      "robotClientUuid": "cfcc5904-2e82-4295-911c-0ce65c9099f2",
      "robotClientName": "ceshi1@csqy1", //机器人名称
      "startTime":"2021-02-03 11:11:11", //该应用开始执行时间
      "endTime": "2021-03-03 12:12:12", //该应用结束执行时间
      "robotName": "导出淘宝订单", //应用名称
      "robotUuid": "xxxxx", //应用uuid
      "status": "finish",
      "idempotentUuid":"xxxx", //幂等id
      "screenshotUrl":"xxxx", //异常截屏
      "result": [ //有输出参数
                {
                    "name": "姓",
                    "value": "王",
                    "type": "str"  //参考应用运行参数枚举说明
                },
                {
                    "name": "名",
                    "value": "5",
                    "type": "str"  //参考应用运行参数枚举说明
                },
                {
                    "name": "上传文件",
                    "value": "https://winrobot-pub-a-dev.oss-cn-hangzhou.aliyuncs.com/document/temp/request.txt",
                    "type": "file"  //参考应用运行参数枚举说明
                }
            ]
    }
  ],
  "msg": "运行结束", //任务运行备注
  "status": "finish", // 任务运行状态
  "taskUuid": "ea947f83-82fb-4afb-8412-4021255fd7cd"
}
```


## 响应

**返回2xx http状态码即可，建议200，影刀接收到2xx状态码后不会进行重试和定时补偿**


### 响应体

无


#### 响应体示例

无

如遇到错误，请跳转到 状态码说明


================================================================================
## 文档路径: 开放API/API接口/JOB运行/启动应用
================================================================================

# 启动应用
路径: 开放API/API接口/JOB运行/启动应用


# 启动应用


## 前置操作

1. 需要使用鉴权接口获取accessToken后，填写到对应的hearder中
2. 新创建的用户账号，必须先进过一次调度模式

需要使用鉴权接口获取accessToken后，填写到对应的hearder中

新创建的用户账号，必须先进过一次调度模式

说明：该接口是指定机器人/机器人分组以及单个应用去运行，适用于简单调度场景，如若涉及到复杂的多个机器人执行多个应用等，建议使用调度任务接口能力，另外获取应用运行信息可通过在控制台-api执行配置回调接口和查询应用运行详情接口获取


### 重点说明

影刀针对输入参数会有大小限制，一般建议所有输入参数加起来不超过8000，如遇到输入参数超过阈值，可有两种方案解决

**方案一:** 可以进行输入参数切割，如电商场景，1000个订单号传进来一次性调用，可以切割成100个订单号进行一次调用，将一次请求转换成10次

**方案二: **可以把长文本转换成文件类型传递

步骤一:打开客户端，修改RPA流程，将字符串类型参数改成文件路径参数类型

步骤二 :保存并发版应用

步骤三:将文本参数转成文件上传到影刀文件服务器(文件上传)，该接口会返回文件key值

步骤三:api调用时，参数类型(type)修改成file类型，传入步骤三获取的文件key值


## 模板


### postMan模板

api启动应用.json（右键另存为）


### Java模板

请求模型：JobStartReq.java（右键另存为）

响应模型：JobStartRep.java（右键另存为）


## 请求

|  |
|  |
| **HTTP URL** | https://api.yingdao.com/oapi/dispatch/v2/job/start | 专有云企业请使用专有云地址 |
| **HTTP Method** | POST |  |

**基本**

**参数值**

**说明**

**HTTP URL**

https://api.yingdao.com/oapi/dispatch/v2/job/start

专有云企业请使用专有云地址

**HTTP Method**

POST



### 请求头

|  |
|  |
| **Authorization** | Bearer {accessToken} | {accessToken}变量需要替换成鉴权接口返回的accessToken |
| **Content-Type** | application/json |  |

**基本**

**参数值**

**说明**

**Authorization**

Bearer {accessToken}

{accessToken}变量需要替换成鉴权接口返回的accessToken

**Content-Type**

application/json



### 请求体

|  |
|  |
| **accountName** | **string** | 机器人账号名称 | 否 | accountName和robotClientGroupUuid互斥，二选一即可，accountName可在控制台-机器人管理列表复制名称 |
| **robotClientGroupUuid** | **string** | 机器人分组名称 | 否 | accountName和robotClientGroupUuid互斥，二选一即可，robotClientGroupUuid可在控制台-机器人分组列表复制UUID |
| **robotUuid** | **string** | 应用uuid | 是 | 登录控制台-应用管理-查看应用详情复制uuid |
| **idempotentUuid** | **string** | 幂等uuid | 否 | 为避免因为网络请求超时，导致重复启动任务，可指定本次请求的幂等uuid，影刀会判断当有多次相同幂等uuid请求时，只会成功过创建一次, **建议使用uuid，另外长度不可超过36位** |
| **waitTimeout** | **string** | 等待超时时间 即将作废 | 否 | 等待超时，可指定job排队时长 等待超时说明 |
| **waitTimeoutSeconds** | **number** | 等待超时时间单位秒 | 否 | 等待超时时间，单位秒，最小设置60(1分钟)，最大设置950400(11天), 默认600(10分钟) |
| **runTimeout** | **number** | 应用运行超时 | 否 | 可用于指定应用运行多长时间后自动停止， 常用来避免应用运行时间不可控或者卡死，影响排队任务运行，**最小设置60 最大设置950400，单位秒，需要配合客户端5.10以及之上版本使用** |
| **priority** | **string** | 排队优先级 | 否 | 可通过该参数指定job在等待排队的优先级，参考等待排队优先级说明，默认是middle |
| **executeScope** | **string** | 执行范围，仅对机器人分组有作用 | 否 | any:机器人分组中随机一个机器人执行all:机器人分组中全部机器人都执行 |
| **params** | **object** | 应用运行参数 | 否 | 共支持五种应用参数，最大支持params长度10000，应用运行参数说明，专有云6.0.0之前版本支持3000，之后版本支持8000 |
| **∟ name** | **string** | 参数名称 | 否 | 参数名称 |
| **∟ value** | **string** | 参数值 | 否 | 参数值 |
| **∟ type** | **string** | 参数类型 | 否 | 参数类型，参考应用运行参数枚举值说明 |

**名称**

**类型**

**说明**

**是否必填**

**描述**

**accountName**

**string**

机器人账号名称

否

accountName和robotClientGroupUuid互斥，二选一即可，accountName可在控制台-机器人管理列表复制名称

**robotClientGroupUuid**

**string**

机器人分组名称

否

accountName和robotClientGroupUuid互斥，二选一即可，robotClientGroupUuid可在控制台-机器人分组列表复制UUID

**robotUuid**

**string**

应用uuid

是

登录控制台-应用管理-查看应用详情复制uuid

**idempotentUuid**

**string**

幂等uuid

否

为避免因为网络请求超时，导致重复启动任务，可指定本次请求的幂等uuid，影刀会判断当有多次相同幂等uuid请求时，只会成功过创建一次, **建议使用uuid，另外长度不可超过36位**

**waitTimeout**

**string**

等待超时时间 即将作废

否

等待超时，可指定job排队时长 等待超时说明

**waitTimeoutSeconds**

**number**

等待超时时间单位秒

否

等待超时时间，单位秒，最小设置60(1分钟)，最大设置950400(11天), 默认600(10分钟)

**runTimeout**

**number**

应用运行超时

否

可用于指定应用运行多长时间后自动停止， 常用来避免应用运行时间不可控或者卡死，影响排队任务运行，**最小设置60 最大设置950400，单位秒，需要配合客户端5.10以及之上版本使用**

**priority**

**string**

排队优先级

否

可通过该参数指定job在等待排队的优先级，参考等待排队优先级说明，默认是middle

**executeScope**

**string**

执行范围，仅对机器人分组有作用

否

any:机器人分组中随机一个机器人执行all:机器人分组中全部机器人都执行

**params**

**object**

应用运行参数

否

共支持五种应用参数，最大支持params长度10000，应用运行参数说明，专有云6.0.0之前版本支持3000，之后版本支持8000

**∟ name**

**string**

参数名称

否

参数名称

**∟ value**

**string**

参数值

否

参数值

**∟ type**

**string**

参数类型

否

参数类型，参考应用运行参数枚举值说明

accountName和robotClientGroupUuid都填写的情况，以机器人分组为准


#### 请求示例


```None
{
  "accountName": "admin@fckj",
  "robotUuid": "73d9a119-7ec7-4226-b679-506afefae667", 
  "idempotentUuid":"69ba7c82-4087-42ca-b1ce-bd117bfea097",
  "waitTimeout":"10m",
  "executeScope":"any",
  "priority": "middle", 
  "params":[
    {
      "name":"获取页数", 
      "value":"10",
      "type":"str" 
    }
  ]
}
```



## 响应


### 响应体

|  |
|  |
| **code** | int | 是 | 状态码 200表示成功，非200表示失败 参考：状态码说明 |
| **success** | boolean | 是 | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **msg** | string | 是 | 状态码描述 |
| **data** | object | 是 | 响应数据 |
| **∟ jobUuid** | string | 是 | 应用运行uuid，用于后续停止运行，查询运行状态入参 |
| **∟ idempotentFlag** | boolean | 是 | 是否幂等创建标识，为true时表示重复请求，配合入参idempotentUuid使用 |

**名称**

**类型**

**是否必填**

**描述**

**code**

int

是

状态码 200表示成功，非200表示失败 参考：状态码说明

**success**

boolean

是

调用是否成功，可以根据该字段判断接口调用是否成功

**msg**

string

是

状态码描述

**data**

object

是

响应数据

**∟ jobUuid**

string

是

应用运行uuid，用于后续停止运行，查询运行状态入参

**∟ idempotentFlag**

boolean

是

是否幂等创建标识，为true时表示重复请求，配合入参idempotentUuid使用



#### 响应体示例


```None
{
    "data": {
        "jobUuid": "fc38f4f1-8444-475e-83f8-3292eeb1606b",
        "idempotentFlag": true
    },
    "code": 200,
    "success": true
}
```

如遇到错误，请跳转到 状态码说明


================================================================================
## 文档路径: 开放API/API接口/JOB运行/查询应用运行结果API
================================================================================

# 查询应用运行结果API
路径: 开放API/API接口/JOB运行/查询应用运行结果API


# 查询应用运行结果


## 前置操作

需要先调用启动应用接口，获取jobUuid

说明：该接口是可以轮询获取job执行状态，一般可以配合回调接口使用，当回调接口收到job相关信息或者轮询到job状态是终态时，需要停止轮询，job运行状态参考应用运行状态枚举值说明，影刀建议的轮询频率是30s一次，


## 模板


### postMan模板

api查询应用运行结果（右键另存为）


### Java模板

请求模型：JobQueryReq（右键另存为）

响应模型：JobQueryRep（右键另存为）

应用运行结果模型:RobotParam（右键另存为）


## 请求

|  |
|  |
| **HTTP URL** | https://api.yingdao.com/oapi/dispatch/v2/job/query | 专有云企业请使用专有云地址 |
| **HTTP Method** | POST |  |

**基本**

**参数值**

**说明**

**HTTP URL**

https://api.yingdao.com/oapi/dispatch/v2/job/query

专有云企业请使用专有云地址

**HTTP Method**

POST



### 请求头

|  |
|  |
| **Authorization** | Bearer {accessToken} | {accessToken}变量需要替换成鉴权接口返回的access Token |
| **Content-Type** | application/json |  |

**基本**

**参数值**

**说明**

**Authorization**

Bearer {accessToken}

{accessToken}变量需要替换成鉴权接口返回的access Token

**Content-Type**

application/json



### 请求体

|  |
|  |
| **jobUuid** | string | 应用运行uuid | 是 | 通过启动job接口获 |

**名称**

**类型**

**说明**

**是否必填**

**描述**

**jobUuid**

string

应用运行uuid

是

通过启动job接口获


#### 请求示例


```None
{
  "jobUuid": "45c882ed-e44f-4818-afc0-05172e7ffbe0"
}
```



## 响应


### 响应体

|  |
|  |
| **code** | int | 是 | 状态码 200表示成功，非200表示失败 参考：状态码说明 |
| **success** | boolean | 是 | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **msg** | string | 是 | 状态码描述 |
| **data** | object | 是 | 响应数据 |
| **∟ jobUuid** | string | 是 | 应用运行uuid |
| **∟ status** | string | 是 | 应用运行状态 |
| **∟ statusName** | string | 是 | 应用运行状态描述 |
| **∟ remark** | string | 否 | 备注信息，当运行异常，值不为空 |
| **∟ robotClientUuid** | string | 否 | 机器人uuid，当应用已被调度之后，值不为空 |
| **∟ robotClientName** | string | 否 | 机器人名称，当应用已被调度之后，值不为空 |
| **∟ startTime** | string | 否 | 应用开始运行时间，当应用开始调度之后，值不为空 |
| **∟ endTime** | string | 否 | 应用结束运行时间，当应用结束调度之后，值不为空 |
| **∟ robotUuid** | string | 是 | 应用uuid |
| **∟ robotName** | string | 是 | 应用名称 |
| **∟ screenshotUrl** | string | 否 | job的截屏url |
| **∟ robotParams** | object | 否 | 应用运行参数 |
| **∟ inputs** | array | 否 | 输入参数 |
| **∟ item** | object | 否 |  |
| **∟ name** | string | 否 | 参数名称 |
| **∟ value** | string | 否 | 参数值 |
| **∟ type** | string | 否 | 参数类型，参考应用运行参数枚举值说明 |

**名称**

**类型**

**是否必填**

**描述**

**code**

int

是

状态码 200表示成功，非200表示失败 参考：状态码说明

**success**

boolean

是

调用是否成功，可以根据该字段判断接口调用是否成功

**msg**

string

是

状态码描述

**data**

object

是

响应数据

**∟ jobUuid**

string

是

应用运行uuid

**∟ status**

string

是

应用运行状态

**∟ statusName**

string

是

应用运行状态描述

**∟ remark**

string

否

备注信息，当运行异常，值不为空

**∟ robotClientUuid**

string

否

机器人uuid，当应用已被调度之后，值不为空

**∟ robotClientName**

string

否

机器人名称，当应用已被调度之后，值不为空

**∟ startTime**

string

否

应用开始运行时间，当应用开始调度之后，值不为空

**∟ endTime**

string

否

应用结束运行时间，当应用结束调度之后，值不为空

**∟ robotUuid**

string

是

应用uuid

**∟ robotName**

string

是

应用名称

**∟ screenshotUrl**

string

否

job的截屏url

**∟ robotParams**

object

否

应用运行参数

**∟ inputs**

array

否

输入参数

**∟ item**

object

否


**∟ name**

string

否

参数名称

**∟ value**

string

否

参数值

**∟ type**

string

否

参数类型，参考应用运行参数枚举值说明



#### 响应体示例

status可用于停止轮询的标识，当状态终态时，需要停止轮询,参考应用运行状态枚举值说明


##### job运行有主流程输入，输出参数


```None
{
    "data": {
        "jobUuid": "42c2e0ce-499b-47aa-8642-3a1125b4759a",
        "status": "waiting",
        "statusName": "等待调度",
        "remark": "应用启动",
        "robotClientUuid": "00a7a1de-af0b-47ad-a3a8-a8fc2b009762",
        "robotClientName": "ceshi1@csqy1",
        "startTime":"2021-02-03 11:11:11", 
        "endTime": "2021-03-03 12:12:12",
        "robotUuid": "00a7a1de-af0b-47ad-a3a8-a8fc2b009761",
        "robotName": "打印日志应用",
        "screenshotUrl": "https://winrobot-pub-a-dev.oss-cn-hangzhou.aliyuncs.com/image/xxx.jpg",
        "robotParams": {
            "inputs": [ 
                {
                    "name": "姓",
                    "value": "王",
                    "type": "str" 
                },
                {
                    "name": "名",
                    "value": "5",
                    "type": "str"  
                },
                {
                    "name": "上传文件",
                    "value": "https://winrobot-pub-a-dev.oss-cn-hangzhou.aliyuncs.com/document/temp/request.txt",
                    "type": "file"  
                }
            ],
          "outputs":[ 
            {
                    "name": "姓",
                    "value": "王",
                    "type": "str"  
            }
          ]
        }
    },
    "code": 200,
    "success": true
}
```


##### job运行无主流程输入，输出参数


```None
{
    "data": {
        "jobUuid": "42c2e0ce-499b-47aa-8642-3a1125b4759a",
        "status": "waiting",
        "statusName": "等待调度",
        "remark": "应用启动",
        "robotClientUuid": "00a7a1de-af0b-47ad-a3a8-a8fc2b009762",
        "robotClientName": "ceshi1@csqy1",
        "startTime":"2021-02-03 11:11:11", 
        "endTime": "2021-03-03 12:12:12",
        "robotUuid": "00a7a1de-af0b-47ad-a3a8-a8fc2b009761",
        "robotName": "打印日志应用"
    }
    "code": 200,
    "success": true
}
```

如遇到错误，请跳转到 状态码说明


================================================================================
## 文档路径: 开放API/API接口/JOB运行/停止应用运行API
================================================================================

# 停止应用运行API
路径: 开放API/API接口/JOB运行/停止应用运行API


# 停止应用运行


## 前置操作

需要先调用启动应用应用接口，获取jobUuid

说明：应用运行状态处于终态，调用该接口无效果


## 模板


### postMan模板

api停止应用运行.json（右键另存为）


### java模板

请求模型：StopJobReq.java（右键另存为）

响应模型：无


## 请求

|  |
|  |
| **HTTP URL** | https://api.yingdao.com/oapi/dispatch/v2/job/stop | 专有云企业请使用专有云地址 |
| **HTTP Method** | POST |  |

**基本**

**参数值**

**说明**

**HTTP URL**

https://api.yingdao.com/oapi/dispatch/v2/job/stop

专有云企业请使用专有云地址

**HTTP Method**

POST



### 请求头

|  |
|  |
| **Authorization** | Bearer {accessToken} | {accessToken}变量需要替换成鉴权接口返回的accessToken |
| **Content-Type** | application/json |  |

**基本**

**参数值**

**说明**

**Authorization**

Bearer {accessToken}

{accessToken}变量需要替换成鉴权接口返回的accessToken

**Content-Type**

application/json



### 请求体

|  |
|  |
| **jobUuid** | string | 应用运行uuid | 是 | 无 |

**名称**

**类型**

**说明**

**是否必填**

**描述**

**jobUuid**

string

应用运行uuid

是

无


#### 请求示例


```None
{
  "jobUuid": "45c882ed-e44f-4818-afc0-05172e7ffbe0"
}
```



## 响应


### 响应体

|  |
|  |
| **code** | int | 是 | 状态码 200表示成功，非200表示失败 参考：状态码说明 |
| **success** | boolean | 是 | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **msg** | string | 是 | 状态码描述 |

**名称**

**类型**

**是否必填**

**描述**

**code**

int

是

状态码 200表示成功，非200表示失败 参考：状态码说明

**success**

boolean

是

调用是否成功，可以根据该字段判断接口调用是否成功

**msg**

string

是

状态码描述



#### 响应体示例

status可用于停止轮询的标识，当状态终态时，需要停止轮询,参考应用运行状态枚举值说明


##### job运行有主流程输入，输出参数


```None
{
    "code": 200,
    "success": true
}
```

如遇到错误，请跳转到 状态码说明


================================================================================
## 文档路径: 开放API/API接口/JOB运行/调度运行记录列表
================================================================================

# 调度运行记录列表
路径: 开放API/API接口/JOB运行/调度运行记录列表


# 调度运行记录列表


## 前置操作

**说明：** 查询调度运行记录列表


## 模板


### postMan模板


### java模板

​   请求模型:

​   响应模型:

​   应用参数模型:


## 请求

|  |
|  |
| **HTTP URL** | https://api.yingdao.com/oapi/dispatch/v2/job/list | 专有云企业请使用专有云地址 |
| **HTTP Method** | **POST** |  |

**基本**


**说明**

**HTTP URL**

https://api.yingdao.com/oapi/dispatch/v2/job/list

专有云企业请使用专有云地址

**HTTP Method**

**POST**



### 请求头

|  |
|  |
| **Authorization** | **Bearer {accessToken}** | **{accessToken}变量需要替换成鉴权接口返回的access Token** |
| **Content-Type** | **application/json** |  |

**基本**


**说明**

**Authorization**

**Bearer {accessToken}**

**{accessToken}变量需要替换成鉴权接口返回的access Token**

**Content-Type**

**application/json**



### 请求体

|  |
|  |
| **robotClientUuid** | **string** | 机器人uuid | 否 | 机器人uuid，通过机器人列表接口获取 |
| **scheduleUuid** | **string** | 计划uuid | 否 | 计划uuid，通过任务列表接口获取 |
| **statusList** | **array** | 状态列表 | 否 | 参考 应用运行状态枚举值说明 |
| **robotUuid** | **string** | 应用uuid | 否 | 应用uuid，通过应用列表接口获取 |
| **triggerTimeBegin** | **string** | 触发时间-起 | 否 | 触发时间-起, 字符串, 格式是 “yyyy-MM-dd HH:mm:ss”, 如, 2025-11-10 10:00:00 |
| **triggerTimeEnd** | **string** | 触发时间-止 | 否 | 触发时间-止, 字符串, 格式是 “yyyy-MM-dd HH:mm:ss”, 如, 2025-11-10 10:00:00 |
| **cursorId** | **long** | 游标id | 否 | 游标id, 当第一页时，默认空 |
| **cursorDirection** | **string** | 翻页方向 | 是 | pre表示往上翻，next表示往下翻，默认为next |
| **size** | **int** | 每页数量 | 是 | 最小是1，最大是100，默认20 |
| **queryApi** | **boolean** | 只查询调度api触发的列表数据 | 否 | 默认false，该参数填写true，能实现控制台api执行记录列表 |

**名称**

**类型**

**说明**

**是否必填**

**描述**

**robotClientUuid**

**string**

机器人uuid

否

机器人uuid，通过机器人列表接口获取

**scheduleUuid**

**string**

计划uuid

否

计划uuid，通过任务列表接口获取

**statusList**

**array**

状态列表

否

参考 应用运行状态枚举值说明

**robotUuid**

**string**

应用uuid

否

应用uuid，通过应用列表接口获取

**triggerTimeBegin**

**string**

触发时间-起

否

触发时间-起, 字符串, 格式是 “yyyy-MM-dd HH:mm:ss”, 如, 2025-11-10 10:00:00

**triggerTimeEnd**

**string**

触发时间-止

否

触发时间-止, 字符串, 格式是 “yyyy-MM-dd HH:mm:ss”, 如, 2025-11-10 10:00:00

**cursorId**

**long**

游标id

否

游标id, 当第一页时，默认空

**cursorDirection**

**string**

翻页方向

是

pre表示往上翻，next表示往下翻，默认为next

**size**

**int**

每页数量

是

最小是1，最大是100，默认20

**queryApi**

**boolean**

只查询调度api触发的列表数据

否

默认false，该参数填写true，能实现控制台api执行记录列表


### 请求示例

**第一页**


```None
{
  "robotClientUuid": "45c882ed-e44f-4818-afc0-05172e7ffbe0",
  "cursorDirection": "next", // 默认往下翻页
  "size": 20
}
```

**向下翻页**


```None
{
  "cursorId": 1234567,  // 取值为nextId
  "robotClientUuid": "45c882ed-e44f-4818-afc0-05172e7ffbe0",
  "cursorDirection": "next",  // 游标方向是next
  "size": 20
}
```

**向上翻页**


```None
{
  "cursorId": 1234567,  // 取值为preId
  "robotClientUuid": "45c882ed-e44f-4818-afc0-05172e7ffbe0",
  "cursorDirection": "pre",  // 游标方向是pre
  "size": 20
}
```



## 响应


### 响应体

|  |
|  |
| **data** | **object** | 是 |  |
| **  ∟code** | **int** | 是 | 状态码 200表示成功，非200表示失败 参考：**状态码说明** |
| **  ∟success** | **boolean** | 是 | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **  ∟msg** | **string** | 是 | 状态码描述 |
| **∟hasData** | **boolean** | 是 | 用于判断继续翻页时是否有数据，可用作翻页按钮置灰操作比如当往下翻页到20页时，第21页没有数据，则在20页时hasData为false，表示不能继续往下翻页，只能往上翻页 |
| **  ∟nextId** | **long** | 是 | 往下翻页时，可作为 cursorId 使用，表示从这个id开始往下翻页 |
| **  ∟preId** | **long** | 是 | 往上翻页时，可作为 cursorId 使用，表示从这个id开始往上翻页 |
| **  ∟cursorDirection** | **string** | 是 | 当前的翻页方向 next表示当前往下翻页 pre表示当前往上翻页 |
| **  ∟dataList** | **array** | 是 | 响应数据 |
| **    ∟ id** | **long** | 是 | 游标id |
| **    ∟ jobUuid** | **string** | 是 | 应用运行uuid |
| **    ∟ taskName** | **string** | 是 | 任务名称 |
| **    ∟ status** | **string** | 是 | 应用运行状态 |
| **    ∟ triggerTime** | **date** | 是 | 触发时间，任务被触发的时间 |
| **    ∟ startTime** | **date** | 否 | 应用开始运行时间，当应用开始调度之后，值不为空 |
| **    ∟ endTime** | **date** | 否 | 应用结束运行时间，当应用结束运行时，值不为空 |
| **    ∟ robotUuid** | **string** | 是 | 应用uuid |
| **    ∟ robotName** | **string** | 是 | 应用名称 |
| **    ∟ remark** | **string** | 否 | 应用运行异常备注 |
| **    ∟ robotClientUuid** | **string** | 否 | 机器人uuid |
| **    ∟ robotClientName** | **string** | 否 | 机器人名称 |

**名称**

**类型**

**是否必填**

**描述**

**data**

**object**

是


**  ∟code**

**int**

是

状态码 200表示成功，非200表示失败 参考：**状态码说明**

**  ∟success**

**boolean**

是

调用是否成功，可以根据该字段判断接口调用是否成功

**  ∟msg**

**string**

是

状态码描述

**∟hasData**

**boolean**

是

用于判断继续翻页时是否有数据，可用作翻页按钮置灰操作比如当往下翻页到20页时，第21页没有数据，则在20页时hasData为false，表示不能继续往下翻页，只能往上翻页

**  ∟nextId**

**long**

是

往下翻页时，可作为 cursorId 使用，表示从这个id开始往下翻页

**  ∟preId**

**long**

是

往上翻页时，可作为 cursorId 使用，表示从这个id开始往上翻页

**  ∟cursorDirection**

**string**

是

当前的翻页方向 next表示当前往下翻页 pre表示当前往上翻页

**  ∟dataList**

**array**

是

响应数据

**    ∟ id**

**long**

是

游标id

**    ∟ jobUuid**

**string**

是

应用运行uuid

**    ∟ taskName**

**string**

是

任务名称

**    ∟ status**

**string**

是

应用运行状态

**    ∟ triggerTime**

**date**

是

触发时间，任务被触发的时间

**    ∟ startTime**

**date**

否

应用开始运行时间，当应用开始调度之后，值不为空

**    ∟ endTime**

**date**

否

应用结束运行时间，当应用结束运行时，值不为空

**    ∟ robotUuid**

**string**

是

应用uuid

**    ∟ robotName**

**string**

是

应用名称

**    ∟ remark**

**string**

否

应用运行异常备注

**    ∟ robotClientUuid**

**string**

否

机器人uuid

**    ∟ robotClientName**

**string**

否

机器人名称


### 响应体示例

status可用于停止轮询的标识，当状态终态时，需要停止轮询,参考应用运行状态枚举值说明

**向下翻页**


```None
{
    "data": {
        "hasData": true,
        "nextId": 284065875470133276,
        "preId": 284065875470133295,
        "cursorDirection": "next",
        "dataList": [
            {
                "id": 284065875470133295,
                "jobUuid": "a54f25ef-9373-499a-afbb-79699f2c39e5",
                "status": "error",
                "taskName": "测试任务3",
                "robotUuid": "db5c419d-0ae7-4104-a313-45dc27ce3e49",
                "robotName": "应用A",
                "triggerTime": "2023-10-24 09:03:00",
                "robotClientUuid": "b21e8ffc-028c-40ae-a074-45a19f07cbda"
            },
            {
                "id": 284065875470133276,
                "jobUuid": "20db77a9-4126-412a-930c-0cc02c7b2d0c",
                "status": "error",
                "taskName": "测试3",
                "robotUuid": "799a2b1d-30ae-40ac-b011-f8a2beea5373",
                "robotName": "迁移测试",
                "triggerTime": "2023-10-24 09:00:00",
                "robotClientUuid": "b21e8ffc-028c-40ae-a074-45a19f07cbda"
            }
        ]
    },
    "code": 200,
    "success": true
}
```

**向上翻页**


```None
{
    "data": {
        "hasData": true,
        "nextId": 464532,
        "preId": 474005,
        "cursorDirection": "pre",
        "dataList": [
            {
                "id": 474005,
                "jobUuid": "118c38ac-c18c-479a-9042-156d46c9a988",
                "status": "stopped",
                "taskName": "task/start",
                "robotUuid": "9c9021d3-42df-4901-9901-9c58340a480c",
                "robotName": "等待-5s",
                "triggerTime": "2023-08-24 17:24:31",
                "robotClientUuid": "cfcc5904-2e82-4295-911c-0ce65c9099f2"
            },
            {
                "id": 464532,
                "jobUuid": "e574c1be-f45f-4bbf-b29f-151271ad6924",
                "status": "error",
                "taskName": "异常应用",
                "robotUuid": "b6519b7e-0b67-4d5a-801e-15e19a7fda1d",
                "robotName": "等待五秒应用",
                "triggerTime": "2023-03-08 21:26:12",
                "robotClientUuid": "a1c19a2c-d9f3-4f77-a6f3-000d79d07db2"
            }
        ]
    },
    "code": 200,
    "success": true
}
```

**如遇到错误，请跳转到****状态码说明**


================================================================================
## 文档路径: 开放API/API接口/JOB运行/应用运行回调
================================================================================

# 应用运行回调
路径: 开放API/API接口/JOB运行/应用运行回调


# 应用运行回调


## 前置操作

1. 使用管理员账号，在影刀控制台登录，在api配置界面配置回调接口；
2. 确保接口是可以正常使用 ；
3. 查看己方服务器环境，如果有防火墙，需要联系技术支持把影刀线上ip加入到白名单中；
4. 需要先调用启动应用接口，获取jobUuid。

使用管理员账号，在影刀控制台登录，在api配置界面配置回调接口；

确保接口是可以正常使用 ；

查看己方服务器环境，如果有防火墙，需要联系技术支持把影刀线上ip加入到白名单中；

需要先调用启动应用接口，获取jobUuid。


说明1：应用运行状态处于终态(正常结束，异常结束，已停止)，影刀服务会主动通过己方配置的回调接口回传job运行结果数据，推荐使用回调方式获取数据结果，保证数据及时获取到。

说明2：当己方回调接口返回2xx状态码时，影刀会任务对方正常接受并处理数据，不会进行定时补偿，当己方返回非2xx状态码时，影刀会每整点定时补偿一次，直到成功或者24次后，结束掉定时补偿，如果碰到job状态超过24小时都没收到回调，建议调用查询应用运行结果。


## 回调对接策略

1. 影刀自身有回调重试功能，当应用运行结束，回调失败后，影刀会在24小时内，整点进行重试，直到重试成功，所以需要对接方在业务层面保障幂等(可以根据jobUuid或者taskUuid进行幂等保障，视startJob时是指定应用和机器人模式还是指定任务模式)；
2. 影刀建议回调接口采用异步方式，先接受到影刀的回调数据后，返回成功，再进行异步处理(提交到线程池中进行异步处理)；
3. 如果为了保障回调数据必达，建议使用回调 + 轮询的方式结合使用，发起startJob后，建议轮询时间2分钟间隔轮询一次(半小时2分钟轮询一次，1小时轮4分钟一次，依次类推，小于半小时建议1分钟)，回调成功后停止轮询。

影刀自身有回调重试功能，当应用运行结束，回调失败后，影刀会在24小时内，整点进行重试，直到重试成功，所以需要对接方在业务层面保障幂等(可以根据jobUuid或者taskUuid进行幂等保障，视startJob时是指定应用和机器人模式还是指定任务模式)；

影刀建议回调接口采用异步方式，先接受到影刀的回调数据后，返回成功，再进行异步处理(提交到线程池中进行异步处理)；

如果为了保障回调数据必达，建议使用回调 + 轮询的方式结合使用，发起startJob后，建议轮询时间2分钟间隔轮询一次(半小时2分钟轮询一次，1小时轮4分钟一次，依次类推，小于半小时建议1分钟)，回调成功后停止轮询。



### 最佳实践

1. job/start成功后，根据jobUuid记录到业务表a(具体命名由对接方定义)中，业务表需要增加jobUuid唯一索引,业务表a至少包含jobUuid , 应用运行状态, 已经失效时间(到期了默认成功，不进行轮询，失效时间建议job/start之后的25小时)；
2. 定时任务轮询该表，按照以上的间隔时间进行轮询，运行状态处于终态或者已经过了25小时后，建议不再轮询；
3. 回调或轮询接受到应用运行状态处于终态(参考应用运行状态枚举值说明)，更新业务表a状态为回调成功；
4. 轮询查询机器人信息接口(视机器人运行应用的时长，30分钟内30s轮询一次，60分钟内轮1分钟一次)，当机器人状态处于空闲之后，可进行任务的job/start, 如果您的机器人较多，建议不要同一时间轮询所有机器人，建议错开轮询。示例: 如果有100台机器人，建议分为100次进行轮询，每个机器人和每个机器人之间1s间隔之后发起轮询，进行错峰。

job/start成功后，根据jobUuid记录到业务表a(具体命名由对接方定义)中，业务表需要增加jobUuid唯一索引,业务表a至少包含jobUuid , 应用运行状态, 已经失效时间(到期了默认成功，不进行轮询，失效时间建议job/start之后的25小时)；

定时任务轮询该表，按照以上的间隔时间进行轮询，运行状态处于终态或者已经过了25小时后，建议不再轮询；

回调或轮询接受到应用运行状态处于终态(参考应用运行状态枚举值说明)，更新业务表a状态为回调成功；

轮询查询机器人信息接口(视机器人运行应用的时长，30分钟内30s轮询一次，60分钟内轮1分钟一次)，当机器人状态处于空闲之后，可进行任务的job/start, 如果您的机器人较多，建议不要同一时间轮询所有机器人，建议错开轮询。

示例: 如果有100台机器人，建议分为100次进行轮询，每个机器人和每个机器人之间1s间隔之后发起轮询，进行错峰。

示例: 如果有100台机器人，建议分为100次进行轮询，每个机器人和每个机器人之间1s间隔之后发起轮询，进行错峰。


## 模板


### postMan模板

api应用运行回调模拟接口.json（右键另存为）


### **java模板**

回调mock接口：FakeCallbackController.java（右键另存为）

回调数据模型：DataTypeResult.java（右键另存为）JobResult.java（右键另存为）

应用运行参数模型：RobotParam.java（右键另存为）

枚举：DataTypeEnum.java（右键另存为）JobStatusEnum.java（右键另存为）


## **请求**

无


### 请求头

|  |
|  |
| **Content-Type** | application/json |  |

**基本**

**参数值**

**说明**

**Content-Type**

application/json



### 请求体

|  |
|  |
| **jobUuid** | string | 是 | 应用运行uuid |
| **dataType** | string | 是 | 回调类型，调用方需要根据该字段，来解析不同回调类型的数据如:当dataType等于job时，表明是job/start接口触发回调，当dataType等于task时，表明是task/start接口触发回调，参考回调数据类型枚举值说明 |
| **status** | string | 是 | 应用运行状态参考 应用运行状态枚举值说明 |
| **msg** | string | 否 | 应用运行信息，当应用运行异常时不为空 |
| **startTime** | string | 是 | 应用运行开始时间 |
| **endTime** | string | 是 | 应用运行结束时间 |
| **robotClientUuid** | string | 是 | 机器人uuid |
| **robotClientName** | string | 是 | 机器人名称 |
| **robotName** | string | 是 | 应用名称 |
| **idempotentUuid** | string | 是 | 本次请求幂等uuid，如果没传随机生成 |
| **result** | array | 否 | 应用运行输出参数 |
| **∟ name** | string | 否 | 参数名称 |
| **∟ value** | string | 否 | 参数值 |
| **∟ type** | string | 否 | 参数类型，参考应用运行参数枚举值说明 |

**名称**

**类型**

**是否必填**

**描述**

**jobUuid**

string

是

应用运行uuid

**dataType**

string

是

回调类型，调用方需要根据该字段，来解析不同回调类型的数据如:当dataType等于job时，表明是job/start接口触发回调，当dataType等于task时，表明是task/start接口触发回调，参考回调数据类型枚举值说明

**status**

string

是

应用运行状态参考 应用运行状态枚举值说明

**msg**

string

否

应用运行信息，当应用运行异常时不为空

**startTime**

string

是

应用运行开始时间

**endTime**

string

是

应用运行结束时间

**robotClientUuid**

string

是

机器人uuid

**robotClientName**

string

是

机器人名称

**robotName**

string

是

应用名称

**idempotentUuid**

string

是

本次请求幂等uuid，如果没传随机生成

**result**

array

否

应用运行输出参数

**∟ name**

string

否

参数名称

**∟ value**

string

否

参数值

**∟ type**

string

否

参数类型，参考应用运行参数枚举值说明



#### 回调示例


```None
{
	"jobUuid": "42c2e0ce-499b-47aa-8642-3a1125b4759a",
	"dataType": "job",
	"status": "finish",
	"msg": "执行结束",
	"robotClientUuid": "bfd28e42-e530-41eb-bf46-796a86ff7ec3",
	"robotClientName": "ceshi1@csqy1",
	"startTime": "2021-02-03 11:11:11",
	"endTime": "2021-03-03 12:12:12",
	"robotName": "导出淘宝订单",
	"robotUuid": "xxxxx",
  	"idempotentUuid":"xxxx",
	"result": [
		{
			"name": "姓",
			"value": "王",
			"type": "str"
		},
		{
			"name": "名",
			"value": "5",
			"type": "str"
		},
		{
			"name": "上传文件",
			"value": "https://winrobot-pub-a-dev.oss-cn-hangzhou.aliyuncs.com/document/temp/request.txt",
			"type": "file"
		}
	]
}
```


## 响应

返回2xx http状态码即可，建议返回200，影刀服务器接受到2xx状态码后，会停止重试和定时补偿


### 响应体

无


#### 响应体示例

无

如遇到错误，请跳转到 状态码说明


================================================================================
## 文档路径: 开放API/API接口/JOB运行/重试应用运行
================================================================================

# 重试应用运行
路径: 开放API/API接口/JOB运行/重试应用运行


## 前置操作

需要先调用启动job接口，获取jobUuid

**说明：应用运行状态非异常或已停止状态，重试无效果**


## 模板


### postMan模板


### java模板

请求模型:

响应模型:

无


## 请求

|  |
|  |
| **HTTP URL** | **https://api.yingdao.com/oapi/dispatch/v2/job/retry** | 专有云企业请使用专有云地址 |
| **HTTP Method** | **POST** |  |

**基本**


**说明**

**HTTP URL**

**https://api.yingdao.com/oapi/dispatch/v2/job/retry**

专有云企业请使用专有云地址

**HTTP Method**

**POST**



## 请求头

|  |
|  |
| **Authorization** | **Bearer {accessToken}** | **{accessToken}变量需要替换成鉴权接口返回的access Token** |
| **Content-Type** | **application/json** |  |

**基本**


**说明**

**Authorization**

**Bearer {accessToken}**

**{accessToken}变量需要替换成鉴权接口返回的access Token**

**Content-Type**

**application/json**



### 请求体

|  |
|  |
| **jobUuid** | **string** | 应用运行uuid | 是 | 无 |

**名称**

**类型**

**说明**

**是否必填**

**描述**

**jobUuid**

**string**

应用运行uuid

是

无



### 请求示例


```None
{
  "jobUuid": "45c882ed-e44f-4818-afc0-05172e7ffbe0"
}
```


## 响应


### 响应体

|  |
|  |
| **code** | **int** | 是 | 状态码 200表示成功，非200表示失败 参考：**状态码说明** |
| **success** | **boolean** | 是 | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **msg** | **string** | 是 | 状态码描述 |

**名称**

**类型**

**是否必填**

**描述**

**code**

**int**

是

状态码 200表示成功，非200表示失败 参考：**状态码说明**

**success**

**boolean**

是

调用是否成功，可以根据该字段判断接口调用是否成功

**msg**

**string**

是

状态码描述



### 响应体示例

status可用于是否显示重试按钮表示，当状态异常或者已停止，显示重试按钮,参考应用运行状态枚举值说明


#### job运行有主流程输入，输出参数


```None
{
    "code": 200,
    "success": true
}
```

**如遇到错误，请跳转到****状态码说明**


================================================================================
## 文档路径: 开放API/API接口/运行日志/查询应用运行日志
================================================================================

# 查询应用运行日志
路径: 开放API/API接口/运行日志/查询应用运行日志


# 查询应用运行日志


## 前置操作

​ 需要先调用启动应用接口，或者通过其他的方式获取jobUuid


## 流程


![None](https://xybot-oss-cdn.yingdao.com/yddoc/rpa_zh-CN/asset/710481967730900992/315f2ce7-eab0-44c1-be58-58c901c20721/img0.png)


## 模板


### postMan模板


### Java模板

​ 请求模型：

​ 响应模型：

​


## 请求

|  |
|  |
| **HTTP URL** | **https://api.yingdao.com/oapi/dispatch/v2/job/log/search** | 专有云企业请使用专有云地址 |
| **HTTP Method** | **POST** |  |

**基本**


**说明**

**HTTP URL**

**https://api.yingdao.com/oapi/dispatch/v2/job/log/search**

专有云企业请使用专有云地址

**HTTP Method**

**POST**




### 请求头

|  |
|  |
| **Authorization** | **Bearer {accessToken}** | **{accessToken}变量需要替换成鉴权接口返回的access Token** |
| **Content-Type** | **application/json** |  |

**基本**


**说明**

**Authorization**

**Bearer {accessToken}**

**{accessToken}变量需要替换成鉴权接口返回的access Token**

**Content-Type**

**application/json**




### 请求体

|  |
|  |
| **jobUuid** | **string** | 应用运行uuid | 是 | 通过启动应用接口获取 |
| **page** | **int** | 第几页 | 否 | 默认第一页 |
| **size** | **int** | 每页几条 | 否 | 默认20条 |
| **queryFilter** |  |  |  |  |
| **   ∟ beginTime** | **string** | 结束时间 | 否 | 开始时间 |
| **   ∟ endTime** | **string** | 开始时间 | 否 | 结束时间 |
| **   ∟ searchKey** | **string** | 查询关键字 | 否 | 查询关键字 |
| **   ∟ sort** | **object** | 排序字段 | 否 |  |
| **     ∟ sortKey** | **string** | 排序key | 否 | 默认time，目前仅支持time |
| **     ∟ sortOrder** | **string** | 排序顺序 | 否 | asc 升序 desc 降序 |

**名称**

**类型**

**说明**

**是否必填**

**描述**

**jobUuid**

**string**

应用运行uuid

是

通过启动应用接口获取

**page**

**int**

第几页

否

默认第一页

**size**

**int**

每页几条

否

默认20条

**queryFilter**





**   ∟ beginTime**

**string**

结束时间

否

开始时间

**   ∟ endTime**

**string**

开始时间

否

结束时间

**   ∟ searchKey**

**string**

查询关键字

否

查询关键字

**   ∟ sort**

**object**

排序字段

否


**     ∟ sortKey**

**string**

排序key

否

默认time，目前仅支持time

**     ∟ sortOrder**

**string**

排序顺序

否

asc 升序 desc 降序



### 请求示例


```None
{
  "jobUuid": "45c882ed-e44f-4818-afc0-05172e7ffbe0",
  "page": 1,
  "size": 20,
  "queryFilter":{
    "beginTime":"2024-03-20 11:11:11",
    "endTime":"2024-03-20 11:11:12",
    "searchKey":"淘宝",
    "sort": {
      "sortKey": "time",
      "sortOrder": "desc"
    }
  }
}
```


## 响应


### 响应体

|  |
|  |
| **code** | **int** | 是 | 状态码 200表示成功，500表示失败 |
| **success** | **boolean** | 是 | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **msg** | **string** | 是 | 状态码描述 |
| **data** | **object** | 是 |  |
| **   ∟requestId** |  |  |  |
| **   ∟ page** |  |  |  |
| **     ∟ total** | **int** | 是 | 总的数量 |
| **     ∟ page** | **int** | 是 | 第几页 |
| **     ∟ size** | **int** | 是 | 一页多少条 |
| **   ∟ logs** | **object** | 否 | 无日志时为空 |
| **     ∟ time** | **string** | 是 | 时间 参考 03/20/2024 15:35:23 |
| **     ∟ level** | **string** | 是 | 日志等级 |
| **     ∟ text** | **string** | 是 | 日志文本，不超过1K，超长会截取 |
| **     ∟ logId** | **long** | 是 | 日志id |

**名称**

**类型**

**是否必填**

**描述**

**code**

**int**

是

状态码 200表示成功，500表示失败

**success**

**boolean**

是

调用是否成功，可以根据该字段判断接口调用是否成功

**msg**

**string**

是

状态码描述

**data**

**object**

是


**   ∟requestId**




**   ∟ page**




**     ∟ total**

**int**

是

总的数量

**     ∟ page**

**int**

是

第几页

**     ∟ size**

**int**

是

一页多少条

**   ∟ logs**

**object**

否

无日志时为空

**     ∟ time**

**string**

是

时间 参考 03/20/2024 15:35:23

**     ∟ level**

**string**

是

日志等级

**     ∟ text**

**string**

是

日志文本，不超过1K，超长会截取

**     ∟ logId**

**long**

是

日志id



### 响应体示例


```None
{
    "requestId":"xxxxx",
    "page": {
            "total": 18,
            "size": 10,
            "page": 1
        },
    "logs": [
            {
                "level": "信息",
                "logId": 1,
                "text": "开始执行...",
                "time": "03/20/2024 15:35:23"
            }
        ],    
      
}
```


### 状态码说明

|  |
|  |
| **200** | 正常 | 调用正常 |
| **500** | 服务端错误 | 服务端错误需要联系技术支持 |
| **80204001** | 日志查询失败 | 表示无法查询日志(原因有机器人未连接，机器人已被删除) |
| **80204004** | 日志查询超时 | 表示 日志查询超时 |

**错误码**

**说明**

**排查建议**

**200**

正常

调用正常

**500**

服务端错误

服务端错误需要联系技术支持

**80204001**

日志查询失败

表示无法查询日志(原因有机器人未连接，机器人已被删除)

**80204004**

日志查询超时

表示 日志查询超时


================================================================================
## 文档路径: 开放API/API接口/运行日志/通知查询应用运行日志
================================================================================

# 通知查询应用运行日志
路径: 开放API/API接口/运行日志/通知查询应用运行日志


# 通知查询应用运行日志


## 前置操作

​ 需要先调用启动应用接口，或者通过其他的方式获取jobUuid


## 模板


### postMan模板


### Java模板

​ 请求模型：

​ 响应模型：

​


## 请求

|  |
|  |
| **HTTP URL** | **https://api.yingdao.com/oapi/dispatch/v2/job/log/notify** | 专有云企业请使用专有云地址 |
| **HTTP Method** | **POST** |  |

**基本**


**说明**

**HTTP URL**

**https://api.yingdao.com/oapi/dispatch/v2/job/log/notify**

专有云企业请使用专有云地址

**HTTP Method**

**POST**




### 请求头

|  |
|  |
| **Authorization** | **Bearer {accessToken}** | **{accessToken}变量需要替换成鉴权接口返回的access Token** |
| **Content-Type** | **application/json** |  |

**基本**


**说明**

**Authorization**

**Bearer {accessToken}**

**{accessToken}变量需要替换成鉴权接口返回的access Token**

**Content-Type**

**application/json**




### 请求体

|  |
|  |
| **jobUuid** | **string** | 应用运行uuid | 是 | 通过启动应用接口获取 |
| **page** | **int** | 第几页 | 否 | 默认第一页 |
| **size** | **int** | 每页几条 | 否 | 默认20条 |
| **queryFilter** |  |  |  |  |
| **   ∟ beginTime** | **string** | 结束时间 | 否 | 开始时间 |
| **   ∟ endTime** | **string** | 开始时间 | 否 | 结束时间 |
| **   ∟ searchKey** | **string** | 查询关键字 | 否 | 查询关键字 |
| **   ∟ sort** | **object** | 排序字段 | 否 |  |
| **     ∟ sortKey** | **string** | 排序key | 否 | 默认time，目前仅支持time |
| **     ∟ sortOrder** | **string** | 排序顺序 | 否 | asc 升序 desc 降序 |

**名称**

**类型**

**说明**

**是否必填**

**描述**

**jobUuid**

**string**

应用运行uuid

是

通过启动应用接口获取

**page**

**int**

第几页

否

默认第一页

**size**

**int**

每页几条

否

默认20条

**queryFilter**





**   ∟ beginTime**

**string**

结束时间

否

开始时间

**   ∟ endTime**

**string**

开始时间

否

结束时间

**   ∟ searchKey**

**string**

查询关键字

否

查询关键字

**   ∟ sort**

**object**

排序字段

否


**     ∟ sortKey**

**string**

排序key

否

默认time，目前仅支持time

**     ∟ sortOrder**

**string**

排序顺序

否

asc 升序 desc 降序


### 请求示例


```None
{
  "jobUuid": "45c882ed-e44f-4818-afc0-05172e7ffbe0",
  "page": 1,
  "size": 20,
  "queryFilter":{
    "beginTime":"2024-03-20 11:11:11",
    "endTime":"2024-03-20 11:11:12",
    "searchKey":"淘宝",
    "sort": {
      "sortKey": "time",
      "sortOrder": "desc"
    }
  }
}
```


## 响应


### 响应体

|  |
|  |
| **code** | **int** | 是 | 状态码 200表示成功，500表示查询失败，80204001表示(无法查询日志) |
| **success** | **boolean** | 是 | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **msg** | **string** | 是 | 状态码描述 |
| **data** | **string** | 是 | **本次请求的requestId** |

**名称**

**类型**

**是否必填**

**描述**

**code**

**int**

是

状态码 200表示成功，500表示查询失败，80204001表示(无法查询日志)

**success**

**boolean**

是

调用是否成功，可以根据该字段判断接口调用是否成功

**msg**

**string**

是

状态码描述

**data**

**string**

是

**本次请求的requestId**


### 响应体示例


```None
{
    "data": "5qwedqeasdc0zssadasdasdqwq",
    "code": 200,
    "success": true
}
```


================================================================================
## 文档路径: 开放API/API接口/运行日志/轮询应用运行日志
================================================================================

# 轮询应用运行日志
路径: 开放API/API接口/运行日志/轮询应用运行日志


# 轮询应用运行日志


## 前置操作

​ 需要先调用通知查询应用运行日志接口，获取requestld

**说明：该接口可以使用requestId，轮询此次的日志，建议每隔100ms轮询一次，轮询超过100次**

**code定义:**

**1.200：表示已经获取到日志**

**2.500: 表示错误，不用轮询**

**3.80204002：表明日志上传处理中，则需要继续轮询**

**ps: 特殊情况，如果** **通知查询应用运行日志** **获取requestId后，超过60s再调用轮询应用运行日志接口，则日志会失效(云端日志保存60s)，则会提示该状态码**


## 模板


### postMan模板


### Java模板

​ 请求模型：

​ 响应模型：

​


## 请求

|  |
|  |
| **HTTP URL** | **https://api.yingdao.com/oapi/dispatch/v2/job/log/query** | 专有云企业请使用专有云地址 |
| **HTTP Method** | **GET** |  |

**基本**


**说明**

**HTTP URL**

**https://api.yingdao.com/oapi/dispatch/v2/job/log/query**

专有云企业请使用专有云地址

**HTTP Method**

**GET**



### 请求头

|  |  |  |
| --- | --- | --- |
|  |
| **Authorization** | **Bearer {accessToken}** | **{accessToken}变量需要替换成鉴权接口返回的access Token** |
| **Content-Type** | **application/json** |  |




**基本**


**说明**

**Authorization**

**Bearer {accessToken}**

**{accessToken}变量需要替换成鉴权接口返回的access Token**

**Content-Type**

**application/json**



### 请求参数

|  |
|  |
| **requestId** | **string** | 应用运行uuid | 是 | 通过启动应用接口获 |

**名称**

**类型**

**说明**

**是否必填**

**描述**

**requestId**

**string**

应用运行uuid

是

通过启动应用接口获


### 请求示例


```None
https://api.yingdao.com/oapi/dispatch/v2/job/log/query?requestId=xxx
```


## 响应


### 响应体

|  |
|  |
| **code** | **int** | 是 | 状态码 200表示成功，500表示失败 , 80204002 表明处理中，需要继续轮询，建议轮询10s，如果还没有日志，则中断 |
| **success** | **boolean** | 是 | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **msg** | **string** | 是 | 状态码描述 |
| **data** | **object** | 是 | 本次请求的requestId |
| **   ∟ requestId** |  |  |  |
| **   ∟ page** |  |  |  |
| **     ∟ total** | **int** | 是 | 总的数量 |
| **     ∟ page** | **int** | 是 | 第几页 |
| **     ∟ size** | **int** | 是 | 一页多少条 |
| **   ∟ logs** | **object** | 否 | 无日志时为空 |
| **     ∟ time** | **string** | 是 | 时间 参考 **03/20/2024 15:35:23** |
| **     ∟ level** | **string** | 是 | 日志等级 |
| **     ∟ text** | **string** | 是 | 日志文本，不超过1K，超长会截取 |
| **     ∟ logId** | **long** | 是 | 日志id |

**名称**

**类型**

**是否必填**

**描述**

**code**

**int**

是

状态码 200表示成功，500表示失败 , 80204002 表明处理中，需要继续轮询，建议轮询10s，如果还没有日志，则中断

**success**

**boolean**

是

调用是否成功，可以根据该字段判断接口调用是否成功

**msg**

**string**

是

状态码描述

**data**

**object**

是

本次请求的requestId

**   ∟ requestId**




**   ∟ page**




**     ∟ total**

**int**

是

总的数量

**     ∟ page**

**int**

是

第几页

**     ∟ size**

**int**

是

一页多少条

**   ∟ logs**

**object**

否

无日志时为空

**     ∟ time**

**string**

是

时间 参考 **03/20/2024 15:35:23**

**     ∟ level**

**string**

是

日志等级

**     ∟ text**

**string**

是

日志文本，不超过1K，超长会截取

**     ∟ logId**

**long**

是

日志id

**80204002 表明处理中，需要继续轮询，建议轮询10s，如果还没有日志，则中断**


### 响应体示例


```None

```



#### 日志输出


```None
{
    "requestId":"xxxxx",
    "page": {
            "total": 18,
            "size": 10,
            "page": 1
        },
    "logs": [
            {
                "level": "信息",
                "logId": 1,
                "text": "开始执行...",
                "time": "03/20/2024 15:35:23"
            }
        ],    
      
}
```


================================================================================
## 文档路径: 开放API/API接口/机器人相关/查询机器人任务队列
================================================================================

# 查询机器人任务队列
路径: 开放API/API接口/机器人相关/查询机器人任务队列


# 查询机器人任务队列


## 前置操作

​ 需要到控制台，或者机器人列表接口获取机器人uuid

**说明：** 查询机器人当前的任务队列


## 模板


### postMan模板


### Java模板

​ 请求模型：

​ 响应模型：

​ 应用参数模型：


## 请求

|  |
|  |
| **HTTP URL** | https://api.yingdao.com/oapi/dispatch/v2/job/list | 专有云企业请使用专有云地址 |
| **HTTP Method** | **POST** |  |

**基本**


**说明**

**HTTP URL**

https://api.yingdao.com/oapi/dispatch/v2/job/list

专有云企业请使用专有云地址

**HTTP Method**

**POST**



### 请求头

|  |
|  |
| **Authorization** | **Bearer {accessToken}** | **{accessToken}变量需要替换成鉴权接口返回的access Token** |
| **Content-Type** | **application/json** |  |

**基本**


**说明**

**Authorization**

**Bearer {accessToken}**

**{accessToken}变量需要替换成鉴权接口返回的access Token**

**Content-Type**

**application/json**



### 请求体

|  |
|  |
| **robotClientUuid** | **string** | 机器人uuid | 是 | 通过启动job接口获取 |
| **cursorId** | **long** | 游标id | 否 | 游标id, 当时第一页时，默认空 |
| **cursorDirection** | **string** | 游标方向 | 是 | pre表示往前翻，next表示往后翻 |
| **size** | **int** | 每页数量 | 是 | 最小是1，最大是100，默认20 |

**名称**

**类型**

**说明**

**是否必填**

**描述**

**robotClientUuid**

**string**

机器人uuid

是

通过启动job接口获取

**cursorId**

**long**

游标id

否

游标id, 当时第一页时，默认空

**cursorDirection**

**string**

游标方向

是

pre表示往前翻，next表示往后翻

**size**

**int**

每页数量

是

最小是1，最大是100，默认20


### 请求示例

**第一页**


```None
{
  "robotClientUuid": "45c882ed-e44f-4818-afc0-05172e7ffbe0",
  "cursorDirection": "next",
  "size": 20
}
```

**下一页**


```None
{
  "cursorId": 1234567,  // 取值为上一页的最后一个id
  "robotClientUuid": "45c882ed-e44f-4818-afc0-05172e7ffbe0",
  "cursorDirection": "next",
  "size": 20
}
```


## 响应


### 响应体

|  |
|  |
| **code** | **int** | 是 | 状态码 200表示成功，非200表示失败 参考：**状态码说明** |
| **success** | **boolean** | 是 | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **msg** | **string** | 是 | 状态码描述 |
| **data** | **array** | 是 | 响应数据 |
| **∟ id** | **long** | 是 | 瀑布流id |
| **∟ jobUuid** | **string** | 是 | 应用运行uuid |
| **∟ taskName** | **string** | 是 | 任务名称 |
| **∟ status** | **string** | 是 | 应用运行状态 |
| **∟ triggerTime** | **date** | 是 | 触发时间，任务被触发的事件 |
| **∟ startTime** | **date** | 否 | 应用开始运行时间，当应用开始调度之后，值不为空 |
| **∟ robotUuid** | **string** | 是 | 应用uuid |
| **∟ robotName** | **string** | 是 | 应用名称 |

**名称**

**类型**

**是否必填**

**描述**

**code**

**int**

是

状态码 200表示成功，非200表示失败 参考：**状态码说明**

**success**

**boolean**

是

调用是否成功，可以根据该字段判断接口调用是否成功

**msg**

**string**

是

状态码描述

**data**

**array**

是

响应数据

**∟ id**

**long**

是

瀑布流id

**∟ jobUuid**

**string**

是

应用运行uuid

**∟ taskName**

**string**

是

任务名称

**∟ status**

**string**

是

应用运行状态

**∟ triggerTime**

**date**

是

触发时间，任务被触发的事件

**∟ startTime**

**date**

否

应用开始运行时间，当应用开始调度之后，值不为空

**∟ robotUuid**

**string**

是

应用uuid

**∟ robotName**

**string**

是

应用名称


### 响应体示例

status可用于停止轮询的标识，当状态终态时，需要停止轮询,参考应用运行状态枚举值说明


#### job运行有主流程输入，输出参数


```None
{
    "data": [{
        "jobUuid": "42c2e0ce-499b-47aa-8642-3a1125b4759a",
        "status": "waiting",
        "statusName": "等待调度",
        "remark": "应用启动",
        "robotClientUuid": "00a7a1de-af0b-47ad-a3a8-a8fc2b009762",
        "robotClientName": "ceshi1@csqy1",
        "startTime":"2021-02-03 11:11:11", 
        "endTime": "2021-03-03 12:12:12",
        "robotUuid": "00a7a1de-af0b-47ad-a3a8-a8fc2b009761",
        "robotName": "打印日志应用",
        "robotParams": {
            "inputs": [ 
                {
                    "name": "姓",
                    "value": "王",
                    "type": "str" 
                },
                {
                    "name": "名",
                    "value": "5",
                    "type": "str"  
                },
                {
                    "name": "上传文件",
                    "value": "https://winrobot-pub-a-dev.oss-cn-hangzhou.aliyuncs.com/document/temp/request.txt",
                    "type": "file"  
                }
            ],
          "outputs":[ 
            {
                    "name": "姓",
                    "value": "王",
                    "type": "str"  
            }
          ]
        }
    }],
    "code": 200,
    "success": true
}
```


#### job运行无主流程输入，输出参数


```None
{
    "data": [{
        "jobUuid": "42c2e0ce-499b-47aa-8642-3a1125b4759a",
        "status": "waiting",
        "statusName": "等待调度",
        "remark": "应用启动",
        "robotClientUuid": "00a7a1de-af0b-47ad-a3a8-a8fc2b009762",
        "robotClientName": "ceshi1@csqy1",
        "startTime":"2021-02-03 11:11:11", 
        "endTime": "2021-03-03 12:12:12",
        "robotUuid": "00a7a1de-af0b-47ad-a3a8-a8fc2b009761",
        "robotName": "打印日志应用"
    }],
    "code": 200,
    "success": true
}
```

**如遇到错误，请跳转到****状态码说明**


================================================================================
## 文档路径: 开放API/API接口/机器人相关/查询机器人分组列表
================================================================================

# 查询机器人分组列表
路径: 开放API/API接口/机器人相关/查询机器人分组列表


# 查询机器人分组列表


## 前置操作

​ 需要使用鉴权接口获取accessToken后，填写到对应的hearder中

**说明：** 该接口查询该租户下机器人分组列表


## 模板


### postMan模板


### Java模板

​ 请求模型：

​ 响应模型：

​


## 请求

|  |
|  |
| **HTTP URL** | **https://****api.yingdao****.com/oapi/dispatch/v2/client/group/list** | 专有云企业请使用专有云地址 |
| **HTTP Method** | **POST** |  |

**基本**


**说明**

**HTTP URL**

**https://****api.yingdao****.com/oapi/dispatch/v2/client/group/list**

专有云企业请使用专有云地址

**HTTP Method**

**POST**



### 请求头

|  |
|  |
| **Authorization** | **Bearer {accessToken}** | **{accessToken}变量需要替换成鉴权接口返回的access Token** |
| **Content-Type** | **application/json** |  |

**基本**


**说明**

**Authorization**

**Bearer {accessToken}**

**{accessToken}变量需要替换成鉴权接口返回的access Token**

**Content-Type**

**application/json**



### 请求体

|  |
|  |
| **key** | **string** | 关键字模糊搜索 效果等同like "xx%"，目前只对机器人分组名称有作用 | 否 | 关键字模糊搜索，支持右边模糊搜索，类似 like "xx%" |
| **page** | **Integer** | 分页参数，第一页 | 否 | 默认1，从第一页开始 |
| **size** | **Integer** | 分页参数，每页多少条 | 否 | 默认20,每页20 |

**名称**

**类型**

**说明**

**是否必填**

**描述**

**key**

**string**

关键字模糊搜索 效果等同like "xx%"，目前只对机器人分组名称有作用

否

关键字模糊搜索，支持右边模糊搜索，类似 like "xx%"

**page**

**Integer**

分页参数，第一页

否

默认1，从第一页开始

**size**

**Integer**

分页参数，每页多少条

否

默认20,每页20


### 请求示例


```None

```

**以机器人uuid查询示例：**


## 响应


### 响应体

|  |
|  |
| **code** | **int** | 是 | 状态码 200表示成功，非200表示失败 参考：**状态码说明** |
| **success** | **boolean** | 是 | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **msg** | **string** | 是 | 状态码描述 |
| **data** | **array** | 是 | 响应数据 |
| **∟ robotClientGroupUuid** | **string** | 是 | 机器人分组uuid |
| **∟ robotClientGroupName** | **string** | 是 | 机器人分组名称 |
| **page** | **objects** | 是 | 分页信息 |
| **∟ total** | **int** | 是 | 分页总数 |
| **∟ size** | **int** | 是 | 每页数量 |
| **∟ page** | **int** | 是 | 第几页 |
| **∟ pages** | **int** | 是 | 分成多少页 |
| **∟ offset** | **int** | 是 | 分页偏移量 |

**名称**

**类型**

**是否必填**

**描述**

**code**

**int**

是

状态码 200表示成功，非200表示失败 参考：**状态码说明**

**success**

**boolean**

是

调用是否成功，可以根据该字段判断接口调用是否成功

**msg**

**string**

是

状态码描述

**data**

**array**

是

响应数据

**∟ robotClientGroupUuid**

**string**

是

机器人分组uuid

**∟ robotClientGroupName**

**string**

是

机器人分组名称

**page**

**objects**

是

分页信息

**∟ total**

**int**

是

分页总数

**∟ size**

**int**

是

每页数量

**∟ page**

**int**

是

第几页

**∟ pages**

**int**

是

分成多少页

**∟ offset**

**int**

是

分页偏移量


### 响应体示例


```None
{
    "data": [
        {
            "uuid": "a3d67252-6795-4a69-99ef-bce60e67xxxd",
            "name": "double11",
        }
    ],
    "page": {
        "total": 5,
        "size": 20,
        "page": 1,
        "pages": 1,
        "offset": 0,
        "order": "desc"
    },
    "code": 200,
    "success": true
}
```

**如遇到错误，请跳转到****状态码说明**


================================================================================
## 文档路径: 开放API/API接口/机器人相关/查询机器人列表
================================================================================

# 查询机器人列表
路径: 开放API/API接口/机器人相关/查询机器人列表


# 查询机器人列表


## 前置操作

​ 需要使用鉴权接口获取accessToken后，填写到对应的hearder中

**说明：** 该接口是可以指定参数筛选查询本企业下的所有机器人，返回字段包括状态，名称，以及机器人所在终端的信息，可用作构建机器人管理模块，也可用于筛选空闲的机器人派发任务


## 模板


### postMan模板


### Java模板

​ 请求模型：

​ 响应模型：


## 请求

|  |
|  |
| **HTTP URL** | **https://****api.yingdao.****com/oapi/dispatch/v2/client/list** | 专有云企业请使用专有云地址 |
| **HTTP Method** | **POST** |  |

**基本**


**说明**

**HTTP URL**

**https://****api.yingdao.****com/oapi/dispatch/v2/client/list**

专有云企业请使用专有云地址

**HTTP Method**

**POST**



### 请求头

|  |
|  |
| **Authorization** | **Bearer {accessToken}** | **{accessToken}变量需要替换成鉴权接口返回的access Token** |
| **Content-Type** | **application/json** |  |

**基本**


**说明**

**Authorization**

**Bearer {accessToken}**

**{accessToken}变量需要替换成鉴权接口返回的access Token**

**Content-Type**

**application/json**



### 请求体

|  |
|  |
| **status** | **string** | 机器人状态 | 否 | 机器人状态，参考机器人状态枚举值说明 |
| **key** | **string** | 关键字模糊搜索，目前只对机器人名称有作用 | 否 | 关键字模糊搜索，目前只对机器人名称有作用，等同于robotClientName |
| **robotClientGroupUuid** | **string** | 机器人分组uuid | 否 | 机器人分组uuid |
| **page** | **Integer** | 分页参数，第一页 | 是 | 第几页 |
| **size** | **Integer** | 分页参数，每页多少条 | 是 | 每页多少条 |

**名称**

**类型**

**说明**

**是否必填**

**描述**

**status**

**string**

机器人状态

否

机器人状态，参考机器人状态枚举值说明

**key**

**string**

关键字模糊搜索，目前只对机器人名称有作用

否

关键字模糊搜索，目前只对机器人名称有作用，等同于robotClientName

**robotClientGroupUuid**

**string**

机器人分组uuid

否

机器人分组uuid

**page**

**Integer**

分页参数，第一页

是

第几页

**size**

**Integer**

分页参数，每页多少条

是

每页多少条


### 请求示例


```None
{
  "page":1,
  "size": 10
}
```


## 响应


### 响应体

|  |
|  |
| **code** | **int** | 是 | 状态码 200表示成功，非200表示失败 参考：**状态码说明** |
| **success** | **boolean** | 是 | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **msg** | **string** | 是 | 状态码描述 |
| **data** | **array** | 是 | 响应数据 |
| **    ∟ robotClientUuid** | **string** | 是 | 机器人uuid |
| **    ∟ robotClientName** | **string** | 是 | 机器人名称，同accountName |
| **    ∟ status** | **string** | 是 | 机器人状态，参考机器人状态枚举值说明 |
| **    ∟ description** | **string** | 否 | 机器人备注描述 |
|  | **object** | 否 | 客户端终端信息 |
| **    ∟ windowsAccount** | **string** | 否 | 客户端系统账号 |
| **    ∟ clientIp** | **string** | 否 | 客户端系统ip |
| **    ∟** **machineName** | **string** | 否 | 客户端系统host名称 |

**名称**

**类型**

**是否必填**

**描述**

**code**

**int**

是

状态码 200表示成功，非200表示失败 参考：**状态码说明**

**success**

**boolean**

是

调用是否成功，可以根据该字段判断接口调用是否成功

**msg**

**string**

是

状态码描述

**data**

**array**

是

响应数据

**    ∟ robotClientUuid**

**string**

是

机器人uuid

**    ∟ robotClientName**

**string**

是

机器人名称，同accountName

**    ∟ status**

**string**

是

机器人状态，参考机器人状态枚举值说明

**    ∟ description**

**string**

否

机器人备注描述


**object**

否

客户端终端信息

**    ∟ windowsAccount**

**string**

否

客户端系统账号

**    ∟ clientIp**

**string**

否

客户端系统ip

**    ∟** **machineName**

**string**

否

客户端系统host名称


### 响应体示例


```None
{
  "data": [{
    "robotClientUuid": "xxx",
    "robotClientName": "admin@ydsc",
    "status": "idle",
    "description": "rpa001",
    "windowsUserName": "by",
    "clientIp": "127.0.0.1",
    "machineName": "RPA-PC"
  }],
  "page": {
    "total": 100,
    "size": 10,
    "page": 1,
    "pages": 10
  },
  "code": 0,
  "success": true,
  "msg": "success"
}
```

**如遇到错误，请跳转到****状态码说明**


================================================================================
## 文档路径: 开放API/API接口/机器人相关/查询机器人信息
================================================================================

# 查询机器人信息
路径: 开放API/API接口/机器人相关/查询机器人信息


# 查询机器人信息


## 前置操作

​ 需要到控制台-机器人管理，复制机器人账号或者机器人uuid，机器人uuid目前需要F12抓包从client/list接口获取uuid

**说明：** 该接口是可以获取机器人信息，支持机器人名称或者机器人uuid查询，只需要填其中一个，两个都填的情况，请确认名称和uuid对的上，建议用机器人名称查询，因为机器人移出后，重新切换调度模式，机器人uuid会生成新的。


## 模板


### postMan模板

api查询机器人信息.json（右键另存为）


### Java模板

​ 请求模型： ​ ClientQueryReq.java（右键另存为）

​ 响应模型： ​ ClientQueryRep.java（右键另存为）


## 请求

|  |
|  |
| **HTTP URL** | **https://****api.yingdao.com****/oapi/dispatch/v2/client/query** | 专有云企业请使用专有云地址 |
| **HTTP Method** | **POST** |  |

**基本**


**说明**

**HTTP URL**

**https://****api.yingdao.com****/oapi/dispatch/v2/client/query**

专有云企业请使用专有云地址

**HTTP Method**

**POST**



### 请求头

|  |
|  |
| **Authorization** | **Bearer {accessToken}** | **{accessToken}变量需要替换成鉴权接口返回的access Token** |
| **Content-Type** | **application/json** |  |

**基本**


**说明**

**Authorization**

**Bearer {accessToken}**

**{accessToken}变量需要替换成鉴权接口返回的access Token**

**Content-Type**

**application/json**



### 请求体

|  |
|  |
| **accountName** | **string** | 机器人账号名称 | 否 | accountName和robotClientUuid互斥，二选一即可，推荐使用机器人账号 |
| **robotClientUuid** | **string** | 机器人uuid | 否 | accountName和robotClientGroupUuid互斥，二选一即可，机器人uuid目前需要F12抓包从client/list接口获取uuid |

**名称**

**类型**

**说明**

**是否必填**

**描述**

**accountName**

**string**

机器人账号名称

否

accountName和robotClientUuid互斥，二选一即可，推荐使用机器人账号

**robotClientUuid**

**string**

机器人uuid

否

accountName和robotClientGroupUuid互斥，二选一即可，机器人uuid目前需要F12抓包从client/list接口获取uuid


### 请求示例

**以机器人账号查询示例：**


```None
{
  "accountName": "boyi@csqy"
}
```

**以机器人uuid查询示例：**


```None
{
  "robotClientUuid": "2cadec88-b93b-4812-8d10-eb2a48ef5111"
}
```


## 响应


### 响应体

|  |
|  |
| **code** | **int** | 是 | 状态码 200表示成功，非200表示失败 参考：**状态码说明** |
| **success** | **boolean** | 是 | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **msg** | **string** | 是 | 状态码描述 |
| **data** | **object** | 是 | 响应数据 |
| **∟ robotClientUuid** | **string** | 是 | 机器人uuid |
| **∟ robotClientName** | **string** | 是 | 机器人名称，同accountName |
| **∟ status** | **string** | 是 | 机器人状态，参考机器人状态枚举值说明 |
| **∟ description** | **string** | 否 | 机器人备注描述 |
| **∟ remark** | **string** | 否 | 运行备注 |
| **∟ clientIp** | **string** | 是 | 客户端ip |

**名称**

**类型**

**是否必填**

**描述**

**code**

**int**

是

状态码 200表示成功，非200表示失败 参考：**状态码说明**

**success**

**boolean**

是

调用是否成功，可以根据该字段判断接口调用是否成功

**msg**

**string**

是

状态码描述

**data**

**object**

是

响应数据

**∟ robotClientUuid**

**string**

是

机器人uuid

**∟ robotClientName**

**string**

是

机器人名称，同accountName

**∟ status**

**string**

是

机器人状态，参考机器人状态枚举值说明

**∟ description**

**string**

否

机器人备注描述

**∟ remark**

**string**

否

运行备注

**∟ clientIp**

**string**

是

客户端ip


### 响应体示例


```None
{
    "data": {
        "robotClientUuid": "0d6a835a-2e08-414a-af73-1e43f9d9c8ff",
        "robotClientName": "ceshi1@csqy1",
        "status": "idle",
        "description": "ceshi1",
        "clientIp": "172.16.28.156", 
      	"remark": "运行成功"
        
    },
    "code": 200,
    "success": true
}
```

**如遇到错误，请跳转到****状态码说明**


================================================================================
## 文档路径: 开放API/API接口/应用相关/查询应用列表API
================================================================================

# 查询应用列表API
路径: 开放API/API接口/应用相关/查询应用列表API


# 查询应用列表

该接口用于分页获取应用列表。


## 前置操作

1. 使用鉴权接口获取accessToken。

使用鉴权接口获取accessToken。


## 请求

|  |
|  |
| **HTTP URL** | https://api.yingdao.com/oapi/app/open/query/list |  |
| **HTTP Method** | POST |  |

**基本**

**参数值**

**说明**

**HTTP URL**

https://api.yingdao.com/oapi/app/open/query/list


**HTTP Method**

POST



### 请求头

|  |
|  |
| **Authorization** | Bearer {accessToken} | {accessToken}变量需要替换成鉴权接口返回的access Token |

**基本**

**参数值**

**说明**

**Authorization**

Bearer {accessToken}

{accessToken}变量需要替换成鉴权接口返回的access Token


### 请求参数

|  |
|  |
| **appId** | String | 否 | 应用ID |
| **size** | String | 否 | 一页大小，默认30，最大100 |
| **page** | String | 否 | 页码 默认1 |
| **ownerUserSearchKey** | String | 否 | 用户搜索关键字，仅支持账号精确匹配 |
| **appName** | String | 否 | 应用名称模糊匹配 |

**名称**

**类型**

**是否必填**

**说明**

**appId**

String

否

应用ID

**size**

String

否

一页大小，默认30，最大100

**page**

String

否

页码 默认1

**ownerUserSearchKey**

String

否

用户搜索关键字，仅支持账号精确匹配

**appName**

String

否

应用名称模糊匹配


## 响应


### 响应数据结构

|  |
|  |
| **data** | object | 结果数据 |
| **    |─ ownerName** | string | 应用所有者名称 |
| **    |─ ownerAccount** | string | 应用所有者账号 |
| **    |─ ownerId** | string | 所有者id |
| **    |─ appId** | string | appid |
| **    |─ appName** | string | 应用名称 |
| **    |─ appTypeName** | string | 应用类型名称 |
| **    |─ appType** | string | 应用类型枚举,app:应用,activity:指令 |
| **    |─ createTime** | string | 创建时间 |
| **    |─ updateTime** | string | 修改时间 |
| **    |─ version** | string | 版本，值内容：未发版、版本 |
| **    |─ supportParam** | boolean | 是否支持应用参数 |
| **    |─ icon** | string | icon图下载地址 |
| **page** | object | 分页信息 |
| **    |─ total** | integer | 总条数 |
| **    |─ size** | integer | 一页大小 |
| **    |─ page** | integer | 当前页 |
| **    |─ pages** | integer | 总页数 |
| **    |─ offset** | integer | 偏移量 |
| **    |─ sortColumn** | string¦ | 用于排序的 column 的名称 |
| **    |─ order** | string¦ | 排序方式 desc/asc，默认降序排列 |
| **code** | integer | 返回结果编码200表示成功，其他表示失败 |
| **success** | boolean | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **requestId** | string | 请求id，方便排查使用 |
| **serverIp** | string | 处理服务的Ip |
| **serverInstName** | string | 处理服务实例名称 |
| **msg** | string | 状态码描述 |

**名称**

**类型**

**说明**

**data**

object

结果数据

**    |─ ownerName**

string

应用所有者名称

**    |─ ownerAccount**

string

应用所有者账号

**    |─ ownerId**

string

所有者id

**    |─ appId**

string

appid

**    |─ appName**

string

应用名称

**    |─ appTypeName**

string

应用类型名称

**    |─ appType**

string

应用类型枚举,app:应用,activity:指令

**    |─ createTime**

string

创建时间

**    |─ updateTime**

string

修改时间

**    |─ version**

string

版本，值内容：未发版、版本

**    |─ supportParam**

boolean

是否支持应用参数

**    |─ icon**

string

icon图下载地址

**page**

object

分页信息

**    |─ total**

integer

总条数

**    |─ size**

integer

一页大小

**    |─ page**

integer

当前页

**    |─ pages**

integer

总页数

**    |─ offset**

integer

偏移量

**    |─ sortColumn**

string¦

用于排序的 column 的名称

**    |─ order**

string¦

排序方式 desc/asc，默认降序排列

**code**

integer

返回结果编码200表示成功，其他表示失败

**success**

boolean

调用是否成功，可以根据该字段判断接口调用是否成功

**requestId**

string

请求id，方便排查使用

**serverIp**

string

处理服务的Ip

**serverInstName**

string

处理服务实例名称

**msg**

string

状态码描述


### 响应数据案例


```None
{
  "data": [
    {
      "ownerName": "测试",
      "ownerAccount": "ceshi2@csqy1",
      "ownerId": "c8d9647f-1435-4b5f-bd64-b5d4613e5283",
      "appId": "107f0f23-3584-4b1b-8665-443605722f95",
      "appName": "kkkkk",
      "appTypeName": "应用",
      "appType": "app",
      "createTime": "2024-05-08 13:49:13",
      "updateTime": "2024-05-08 13:50:24",
      "version": "1",
      "supportParam": false,
      "icon": ""
    }
  ],
  "page": {
    "total": 1,
    "size": 30,
    "page": 1,
    "pages": 30,
    "offset": 0,
    "order": "desc"
  },
  "code": 200,
  "success": true,
  "requestId": "2f296259-d953-4939-9701-bc28ac43e002"
}
```


## 使用示例


### curl示例


```None
curl --location --request POST 'https://api.yingdao.com/oapi/app/open/query/list' \ --header 'Authorization: Bearer 2eed910f-6ade-4e0c-9007-0feade4f5df6' \ \ --header 'Content-Type: application/json' \ --header 'Accept: */*' \ --header 'Host: api.yingdao.com' \ --header 'Connection: keep-alive' \ --data-raw '{ "appId": "string", "size": "30", "page": "1", "ownerUserSearchKey": "string", "appName": "string" }'
```


================================================================================
## 文档路径: 开放API/API接口/应用相关/查询应用运行记录API
================================================================================

# 查询应用运行记录API
路径: 开放API/API接口/应用相关/查询应用运行记录API


# 查询应用运行记录

该接口用于分页获取应用运行记录。


## 前置操作

1. 使用鉴权接口获取accessToken。

使用鉴权接口获取accessToken。


## 请求

|  |
|  |
| **HTTP URL** | https://api.yingdao.com/oapi/app/open/query/use/record/list |  |
| **HTTP Method** | POST |  |

**基本**

**参数值**

**说明**

**HTTP URL**

https://api.yingdao.com/oapi/app/open/query/use/record/list


**HTTP Method**

POST



### 请求头

|  |
|  |
| **Authorization** | Bearer {accessToken} | {accessToken}变量需要替换成鉴权接口返回的access Token |

**基本**

**参数值**

**说明**

**Authorization**

Bearer {accessToken}

{accessToken}变量需要替换成鉴权接口返回的access Token


### 请求参数

|  |
|  |
| **appId** | String | 否 | 应用ID,与时间筛选必传其中之一 |
| **size** | String | 否 | 一页大小，默认30，最大100 |
| **minId** | int | 是 | 游标分页字段，每次传上一页最大id作为起始id，不传则minId默认0 |
| **beginDate** | String | 否 | 开始时间,与应用筛选必传其中之一 |
| **endDate** | String | 否 | 结束时间,与应用筛选必传其中之一 |

**名称**

**类型**

**是否必填**

**说明**

**appId**

String

否

应用ID,与时间筛选必传其中之一

**size**

String

否

一页大小，默认30，最大100

**minId**

int

是

游标分页字段，每次传上一页最大id作为起始id，不传则minId默认0

**beginDate**

String

否

开始时间,与应用筛选必传其中之一

**endDate**

String

否

结束时间,与应用筛选必传其中之一


## 响应


### 响应数据结构

|  |
|  |
| **code** | integer | 返回结果编码200表示成功，其他表示失败 |
| **success** | boolean | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **requestId** | string | 请求ID |
| **msg** | string | 状态码描述 |
| **data** | [object] | 结果数据 |
| **    |─id** | number | 主键id 分页时需要带上当前页的最后一条数据的id |
| **    |─ runRecordId** | string | 运行记录对外主键 |
| **    |─ appId** | string | 应用id |
| **    |─ userName** | string | 账号 |
| **    |─ startTime** | string | 运行开始时间 |
| **    |─ endTime** | string | 运行结束时间 |
| **    |─ updateTime** | string | 同步时间 |
| **    |─ runStatusName** | string | 运行状态 |
| **    |─ runStatus** | string | 运行状态 |
| **    |─ runningTime** | integer | 运行时间长（秒） |
| **    |─ heartTime** | string | 心跳时间 |
| **    |─ appName** | string | 应用名 |
| **    |─ startMode** | string | 触发启动方式 |
| **    |─ startModeName** | string | 触发启动方式 名称 |
| **    |─ remark** | string | 错误信息 |
| **page** | [PageDTO | 分页信息 |
| **    |─ total** | integer | 总条数 |
| **    |─ size** | integer | 一页大小 |
| **    |─ page** | integer | 当前页 |
| **    |─ pages** | integer | 总页数 |
| **    |─ offset** | integer | 偏移量 |
| **    |─ sortColumn** | string¦ | 用于排序的 column 的名称 |
| **    |─ order** | string¦ | 排序方式 desc/asc，默认降序排列 |
| **code** | integer | 返回结果编码200表示成功，其他表示失败 |
| **success** | boolean | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **requestId** | string | 请求id，方便排查使用 |
| **serverIp** | string | 处理服务的Ip |
| **serverInstName** | string | 处理服务实例名称 |
| **msg** | string | 状态码描述 |

**名称**

**类型**

**说明**

**code**

integer

返回结果编码200表示成功，其他表示失败

**success**

boolean

调用是否成功，可以根据该字段判断接口调用是否成功

**requestId**

string

请求ID

**msg**

string

状态码描述

**data**

[object]

结果数据

**    |─id**

number

主键id 分页时需要带上当前页的最后一条数据的id

**    |─ runRecordId**

string

运行记录对外主键

**    |─ appId**

string

应用id

**    |─ userName**

string

账号

**    |─ startTime**

string

运行开始时间

**    |─ endTime**

string

运行结束时间

**    |─ updateTime**

string

同步时间

**    |─ runStatusName**

string

运行状态

**    |─ runStatus**

string

运行状态

**    |─ runningTime**

integer

运行时间长（秒）

**    |─ heartTime**

string

心跳时间

**    |─ appName**

string

应用名

**    |─ startMode**

string

触发启动方式

**    |─ startModeName**

string

触发启动方式 名称

**    |─ remark**

string

错误信息

**page**

[PageDTO

分页信息

**    |─ total**

integer

总条数

**    |─ size**

integer

一页大小

**    |─ page**

integer

当前页

**    |─ pages**

integer

总页数

**    |─ offset**

integer

偏移量

**    |─ sortColumn**

string¦

用于排序的 column 的名称

**    |─ order**

string¦

排序方式 desc/asc，默认降序排列

**code**

integer

返回结果编码200表示成功，其他表示失败

**success**

boolean

调用是否成功，可以根据该字段判断接口调用是否成功

**requestId**

string

请求id，方便排查使用

**serverIp**

string

处理服务的Ip

**serverInstName**

string

处理服务实例名称

**msg**

string

状态码描述


### 响应数据案例


```None
{
  "data": [
    {
      "runRecordId": "360164898894159873",
      "appId": "29deebe9-95fc-4bdd-a6ba-df362d7215e0",
      "userName": "ceshi3@csqy1",
      "startTime": "2024-05-07 20:46:43",
      "endTime": "2024-05-07 20:46:44",
      "updateTime": "24-5-7 下午8:46",
      "runStatus": "finish",
      "runningTime": 1,
      "heartTime": "2024-05-07 20:46:44",
      "appName": "kkkkkk",
      "startMode": "manual",
      "startModeName": "手工执行"
    }
  ],
  "code": 200,
  "success": true,
  "requestId": "8c7a95ea-4ee6-4f01-8bcb-0925658ac283"
}
```


## 使用示例


### curl示例


```None
curl --location --request POST 'https://api.yingdao.com/oapi/app/open/query/use/record/list' \ --header 'Authorization: Bearer 2eed910f-6ade-4e0c-9007-0feade4f5df6' \ \ --header 'Content-Type: application/json' \ --header 'Accept: */*' \ --header 'Host: api.yingdao.com' \ --header 'Connection: keep-alive' \ --data-raw '{ "appId": "string", "size": "30", "minId": "0", "beginDate": "2024-05-15 00:00:00", "endDate": "2024-05-15 23:00:00" }'
```


================================================================================
## 文档路径: 开放API/API接口/应用相关/查询应用主流程参数结构API
================================================================================

# 查询应用主流程参数结构API
路径: 开放API/API接口/应用相关/查询应用主流程参数结构API


# 查询应用主流程参数结构


## 前置操作

需要使用鉴权接口获取accessToken后，填写到对应的hearder中

说明：该接口只能查询已经发版过的应用


## 模板


### postMan模板

api查询应用主流程参数结构.json（右键另存为）


### java模板

请求模型:

无

响应模型:

RobotParamInfo.java（右键另存为）

RobotParamOpenApiBO.java（右键另存为）


## 请求

|  |
|  |
| **HTTP URL** | https://api.yingdao.com/oapi/robot/v2/queryRobotParam | 专有云企业请使用专有云地址 |
| **HTTP Method** | GET |  |

**基本**


**说明**

**HTTP URL**

https://api.yingdao.com/oapi/robot/v2/queryRobotParam

专有云企业请使用专有云地址

**HTTP Method**

GET



## 请求头

|  |
|  |
| **Authorization** | Bearer {accessToken} | accessToken变量需要替换成鉴权接口返回的access Token |

**基本**


**说明**

**Authorization**

Bearer {accessToken}

accessToken变量需要替换成鉴权接口返回的access Token


### 请求表单

|  |
|  |
| **robotUuid** | string | 应用uuid | 否 |  |
| **accurateRobotName** | string | 精确匹配的应用名称 | 否 | 该参数是精确匹配应用名称，不支持模糊查询 |

**名称**

**类型**

**说明**

**是否必填**

**描述**

**robotUuid**

string

应用uuid

否


**accurateRobotName**

string

精确匹配的应用名称

否

该参数是精确匹配应用名称，不支持模糊查询



### 请求示例

根据应用uuid查询应用主流程参数结构

https://api.yingdao.com/oapi/robot/v2/queryRobotParam?robotUuid=xxxxx

根据精确的应用名称查询应用主流程参数结构

https://api.yingdao.com/oapi/robot/v2/queryRobotParam?accurateRobotName=xxx


## 响应


### 响应体

|  |
|  |
| **code** | int | 是 | 状态码 200表示成功，非200表示失败 参考：状态码说明 |
| **success** | boolean | 是 | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **msg** | string | 是 | 状态码描述 |
| **data** | array | 是 | 响应数据 |
| **∟ inputParams** | array | 否 | 入参 |
| **∟ name** | string | 是 | 参数名称 |
| **∟ direction** | string | 是 | 入参出参 |
| **∟ type** | string | 是 | 参数类型，str,float,file等五种类型 |
| **∟ value** | string | 是 | 参数值 |
| **∟ description** | string | 是 | 参数描述 |
| **∟ kind** | string | 是 | kind是客户端的一个定义，目前不需要。字符串他对应的类型是Text |
| **∟ outputParams** | array |  | 出参同上 |
| **∟ 同上** |  |  |  |

**名称**

**类型**

**是否必填**

**描述**

**code**

int

是

状态码 200表示成功，非200表示失败 参考：状态码说明

**success**

boolean

是

调用是否成功，可以根据该字段判断接口调用是否成功

**msg**

string

是

状态码描述

**data**

array

是

响应数据

**∟ inputParams**

array

否

入参

**∟ name**

string

是

参数名称

**∟ direction**

string

是

入参出参

**∟ type**

string

是

参数类型，str,float,file等五种类型

**∟ value**

string

是

参数值

**∟ description**

string

是

参数描述

**∟ kind**

string

是

kind是客户端的一个定义，目前不需要。字符串他对应的类型是Text

**∟ outputParams**

array


出参同上

**∟ 同上**





### 响应体示例


```None
{
    "data": {
        "inputParams": [
            {
                "name": "input_str_variable",
                "direction": "In",
                "type": "str",
                "value": "",
                "description": "",
                "kind": "Text"
            },
            {
                "name": "input_float_variable",
                "direction": "In",
                "type": "float",
                "value": "0.0",
                "description": "",
                "kind": "Expression"
            },
            {
                "name": "input_bool_variable",
                "direction": "In",
                "type": "bool",
                "value": "False",
                "description": "",
                "kind": "Expression"
            },
            {
                "name": "input_file_variable",
                "direction": "In",
                "type": "file",
                "value": "",
                "description": "",
                "kind": "Text"
            }
        ],
        "outputParams": [
            {
                "name": "input_int_variable",
                "direction": "Out",
                "type": "int",
                "value": "0",
                "description": "",
                "kind": "Expression"
            }
        ]
    },
    "code": 200,
    "success": true,
    "requestId": "7484425d-2525-4b58-aacf-50f205a603fd"
}
```

如遇到错误，请跳转到 状态码说明


================================================================================
## 文档路径: 开放API/API接口/应用相关/转移应用所有者API
================================================================================

# 转移应用所有者API
路径: 开放API/API接口/应用相关/转移应用所有者API


# 转移应用所有者

该接口用于转移应用所有者到接收人。


## 前置操作

1. 使用鉴权接口获取accessToken。

使用鉴权接口获取accessToken。


## 请求

|  |
|  |
| **HTTP URL** | https://api.yingdao.com/oapi/app/open/translate/owner |  |
| **HTTP Method** | POST |  |

**基本**

**参数值**

**说明**

**HTTP URL**

https://api.yingdao.com/oapi/app/open/translate/owner


**HTTP Method**

POST



### 请求头

|  |
|  |
| **Authorization** | Bearer {accessToken} | {accessToken}变量需要替换成鉴权接口返回的access Token |

**基本**

**参数值**

**说明**

**Authorization**

Bearer {accessToken}

{accessToken}变量需要替换成鉴权接口返回的access Token


### 请求参数

|  |
|  |
| **appId** | String | 是 | 应用ID |
| **receiveUserAccount** | String | 是 | 接收人账号，需要精确匹配 |

**名称**

**类型**

**是否必填**

**说明**

**appId**

String

是

应用ID

**receiveUserAccount**

String

是

接收人账号，需要精确匹配



## 响应


### 响应数据结构

|  |
|  |
| **code** | integer | 返回结果编码200表示成功，其他表示失败 |
| **success** | boolean | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **requestId** | string | 请求ID |
| **msg** | string | 状态码描述 |

**名称**

**类型**

**说明**

**code**

integer

返回结果编码200表示成功，其他表示失败

**success**

boolean

调用是否成功，可以根据该字段判断接口调用是否成功

**requestId**

string

请求ID

**msg**

string

状态码描述


### 响应数据案例


```None
{
  "code": 200,
  "success": true,
  "requestId": "102778b7-3751-4d82-b919-e9d03f347f87"
}
```


## 使用示例


### curl示例


```None
curl --location --request POST 'https://api.yingdao.com/oapi/app/open/translate/owner' \ --header 'Authorization: Bearer 2eed910f-6ade-4e0c-9007-0feade4f5df6' \ \ --header 'Content-Type: application/json' \ --header 'Accept: */*' \ --header 'Host: api.yingdao.com' \ --header 'Connection: keep-alive' \ --data-raw '{ "appId": "111111", "receiveUserAccount": "ceshi@ceshi" }'
```


================================================================================
## 文档路径: 开放API/API接口/文件/文件上传
================================================================================

# 文件上传
路径: 开放API/API接口/文件/文件上传


# 文件上传


## 前置操作

​ 需要使用鉴权接口获取accessToken后，填写到对应的hearder中

**说明：** 该接口用于调度api 启动任务或者启动应用场景，当流程中有文件类型输入参数时，先调用该接口完成文件上传，该接口会返回一个文件key，将文件key作为输入参数传递即可，影刀调度程序会根据文件key构建成机器人可识别下载的文件url。


## 上传限制

- 单次上传不能超过10M
- 目前支持txt,csv,xslx文件类型
- 文件名建议在100长度之内，超出会限制
- 文件有效期7天，超过7天后文件不可访问，该限制可能会导致超过7天的job再发起重试，机器人运行应用会失败
- 单企业文件上传额度限制100M，意味着7天内单个企业累积上传不能超过100M
- 文件失效后，文件上传额度15分钟内回收

单次上传不能超过10M

目前支持txt,csv,xslx文件类型

文件名建议在100长度之内，超出会限制

文件有效期7天，超过7天后文件不可访问，该限制可能会导致超过7天的job再发起重试，机器人运行应用会失败

单企业文件上传额度限制100M，意味着7天内单个企业累积上传不能超过100M

文件失效后，文件上传额度15分钟内回收


## 请求

|  |
|  |
| **HTTP URL** | https://api.yingdao.com/oapi/dispatch/v2/file/upload | 专有云企业请使用专有云地址 |
| **HTTP Method** | **POST** |  |

**基本**


**说明**

**HTTP URL**

https://api.yingdao.com/oapi/dispatch/v2/file/upload

专有云企业请使用专有云地址

**HTTP Method**

**POST**



### 请求头

|  |
|  |
| **Authorization** | **Bearer {accessToken}** | {accessToken} **变量需要替换成鉴权接口返回的access Token** |
| **Content-Type** | **multipart/form-data** |  |

**基本**


**说明**

**Authorization**

**Bearer {accessToken}**

{accessToken} **变量需要替换成鉴权接口返回的access Token**

**Content-Type**

**multipart/form-data**



### 请求参数

| **名称** | **类型** | **是否必填** | 说明 |
| --- | --- | --- | --- |
| file | file | 是 | 目标文件的二进制文件流，具体用法请参照上面的示例代码 |

**名称**

**类型**

**是否必填**

说明

file

file

是

目标文件的二进制文件流，具体用法请参照上面的示例代码


## 响应


### 响应体

|  |
|  |
| **code** | **int** | 是 | 状态码 200表示成功，非200表示失败 参考：**状态码说明** |
| **success** | **boolean** | 是 | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **msg** | **string** | 是 | 状态码描述 |
| **data** | **object** | 是 | 响应数据 |
| ** ∟fileKey** | **string** | 是 | 文件fileKey， 用于启动应用和启动任务 文件类型输入参数 |

**名称**

**类型**

**是否必填**

**描述**

**code**

**int**

是

状态码 200表示成功，非200表示失败 参考：**状态码说明**

**success**

**boolean**

是

调用是否成功，可以根据该字段判断接口调用是否成功

**msg**

**string**

是

状态码描述

**data**

**object**

是

响应数据

** ∟fileKey**

**string**

是

文件fileKey， 用于启动应用和启动任务 文件类型输入参数


### 响应体示例

**成功响应**


```None
{
    "data": {
        "fileKey": "aedbdaad-0c9c-4c05-9082-be42f0ba03a2"
    },
    "code": 200,
    "success": true
}
```

**失败响应**

不支持的文件类型


```None
{
    "code": 80204003,
    "success": false,
    "requestId": "eb9bf57c1e54ea2668e06ae1",
    "serverInstName": "xybot-dispatch",
    "msg": "原因:仅支持:txt,csv,xlsx 文件类型，文件上传失败"
}
```

单次上传超过限制


```None
{
    "code": 80200010,
    "success": false,
    "requestId": "7be91cbdd8451eabf187d66c",
    "serverInstName": "xybot-dispatch",
    "msg": "文件大小超出限制，阈值:3145728"
}
```

文件名超长(超过100行限制)


```None
{
    "code": 80204003,
    "success": false,
    "requestId": "7129aee8c0feba6860bf4837",
    "serverInstName": "xybot-dispatch",
    "msg": "原因:文件名超长，文件上传失败"
}
```

文件累计使用量超过限制(累计100M)


```None
{
    "code": 80204003,
    "success": false,
    "requestId": "1638a2c5a3910a564c8a6d91",
    "serverInstName": "xybot-dispatch",
    "msg": "文件存储使用量超过限制，1048576"
}
```

|  |
|  |
| **200** | 正常 | 调用正常 |
| **500** | 服务端错误 | 服务端错误需要联系技术支持 |
| **80204003** | 文件上传失败 | 根据文案中的提示进行处理，一般是上述限制条件被触发导致的失败 |
| **80200010** | 单词文件上传过大 | 文件大小超出限制，阈值:3145728 |

**错误码**

**说明**

**排查建议**

**200**

正常

调用正常

**500**

服务端错误

服务端错误需要联系技术支持

**80204003**

文件上传失败

根据文案中的提示进行处理，一般是上述限制条件被触发导致的失败

**80200010**

单词文件上传过大

文件大小超出限制，阈值:3145728



## 示例代码


### Python


```javascript
import requests

url = "https://api.yingdao.com/oapi/dispatch/v2/file/upload"

payload={}
files=[
   ('file',('file',open('/path/to/file','rb'),'application/octet-stream'))
]
headers = {
   'Authorization': 'Bearer {accessToken}'
}

response = requests.request("POST", url, headers=headers, data=payload, files=files)

print(response.text)
```


### Curl


```javascript
curl --location --request POST 'https://api.yingdao.com/oapi/dispatch/v2/file/upload' \
--header 'Authorization: Bearer {accessToken}' \
--form 'file=@"/path/to/file"'
```


![None](https://xybot-oss-cdn.yingdao.com/yddoc/rpa_zh-CN/asset/710470879923273728/51c7acdb-7afc-4019-b376-ef061ddff6c3/img0.png)



**如遇到错误，请跳转到****状态码说明**


================================================================================
## 文档路径: 开放API/API接口/任务/查询任务&机器人应用运行详情
================================================================================

# 查询任务&机器人应用运行详情
路径: 开放API/API接口/任务/查询任务&机器人应用运行详情


# 查询任务&机器人应用运行详情


## 前置操作

​ **1.调用该接口前请获取taskUuid以及robotClientUuid，作为参数，可通过查询单个任务详情接口以及最新执行记录接口获取taskUuid和robotClientUuid**

**说明：** 该接口可以获取该条任务运行记录下该客户端所有应用运行记录，等同于中控平台执行记录-点击具体某个客户端查看应用运行详情


## 模板


### postMan模板


### Java模板

​ 请求模型：

​ 响应模型：

​ 应用运行结果模型：


## 请求

|  |
|  |
| **HTTP URL** | **https://api.winrobot360.com/oapi/dispatch/v2/task/process/detail** | 专有云企业请使用专有云地址 |
| **HTTP Method** | **POST** |  |

**基本**


**说明**

**HTTP URL**

**https://api.winrobot360.com/oapi/dispatch/v2/task/process/detail**

专有云企业请使用专有云地址

**HTTP Method**

**POST**



### 请求头

|  |
|  |
| **Authorization** | **Bearer {accessToken}** | **{accessToken}变量需要替换成鉴权接口返回的access Token** |
| **Content-Type** | **application/json** |  |

**基本**


**说明**

**Authorization**

**Bearer {accessToken}**

**{accessToken}变量需要替换成鉴权接口返回的access Token**

**Content-Type**

**application/json**



### 请求体

|  |
|  |
| **taskUuid** | **string** | 任务运行uuid | 是 | 通过启动任务接口获取 |
| **robotClientUuid** | **string** | 机器人uuid | 是 | 机器人uuid |

**名称**

**类型**

**说明**

**是否必填**

**描述**

**taskUuid**

**string**

任务运行uuid

是

通过启动任务接口获取

**robotClientUuid**

**string**

机器人uuid

是

机器人uuid


### 请求示例


```None
{
  "taskUuid": "45c882ed-e44f-4818-afc0-05172e7ffbe0",
  "robotClientUuid": "b2c85558-2ecb-421c-99db-fa2bd56de123"
}
```


## 响应


### 响应体

|  |
|  |
| **code** | **int** | 是 | 状态码 200表示成功，非200表示失败 参考：**状态码说明** |
| **success** | **boolean** | 是 | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **msg** | **string** | 是 | 状态码描述 |
| **data** | **object** | 是 | 响应数据 |
| **∟** **jobList** | **array** | 是 | 应用运行记录 |
| **∟** **jobUuid** | **string** | 是 | 应用运行记录uuid |
| **∟** **taskUuid** | **string** | 是 | 任务运行记录uuid |
| **∟** **status** | **string** | 是 | 应用运行状态，参考应用运行状态枚举值说明 |
| **∟** **createTime** | **string** | 是 | 创建时间，一般等同触发时间 |
| **∟** **updateTime** | **string** | 是 | 更新时间 |
| **∟** **dispatchCount** | **number** | 是 | 调度次数 |
| **∟** **startTime** | **string** | 否 | 开始运行时间 格式yyyy-mm-dd hh:MM:dd |
| **∟** **endTime** | **string** | 否 | 结束运行时间 格式yyyy-mm-dd hh:MM:dd |
| **∟** **existsParam** | **boolean** | 是 | 是否存在输入/输出参数 |
| **∟** **priority** | **string** | 是 | 优先级 参考优先级枚举值说明 |
| **∟** **remark** | **string** | 是 | 应用运行备注，当应用运行报错后，该字段可视为错误备注 |
| **∟** **robotClientUuid** | **string** | 是 | 机器人uuid |
| **∟** **robotName** | **string** | 是 | 应用名称 |
| **∟** **robotUuid** | **string** | 是 | 应用uuid |
| **∟** **screenshotUrl** | **string** | 否 | 错误截屏url，当应用运行某一次运行异常后，值不为空 |
| **∟** **sourceType** | **string** | 是 | 执行来源类型，参考执行来源类型枚举值说明 |
| **∟** **sourceUuid** | **string** | 是 | 执行来源uuid，一般等同于scheduleUuid |

**名称**

**类型**

**是否必填**

**描述**

**code**

**int**

是

状态码 200表示成功，非200表示失败 参考：**状态码说明**

**success**

**boolean**

是

调用是否成功，可以根据该字段判断接口调用是否成功

**msg**

**string**

是

状态码描述

**data**

**object**

是

响应数据

**∟** **jobList**

**array**

是

应用运行记录

**∟** **jobUuid**

**string**

是

应用运行记录uuid

**∟** **taskUuid**

**string**

是

任务运行记录uuid

**∟** **status**

**string**

是

应用运行状态，参考应用运行状态枚举值说明

**∟** **createTime**

**string**

是

创建时间，一般等同触发时间

**∟** **updateTime**

**string**

是

更新时间

**∟** **dispatchCount**

**number**

是

调度次数

**∟** **startTime**

**string**

否

开始运行时间 格式yyyy-mm-dd hh:MM:dd

**∟** **endTime**

**string**

否

结束运行时间 格式yyyy-mm-dd hh:MM:dd

**∟** **existsParam**

**boolean**

是

是否存在输入/输出参数

**∟** **priority**

**string**

是

优先级 参考优先级枚举值说明

**∟** **remark**

**string**

是

应用运行备注，当应用运行报错后，该字段可视为错误备注

**∟** **robotClientUuid**

**string**

是

机器人uuid

**∟** **robotName**

**string**

是

应用名称

**∟** **robotUuid**

**string**

是

应用uuid

**∟** **screenshotUrl**

**string**

否

错误截屏url，当应用运行某一次运行异常后，值不为空

**∟** **sourceType**

**string**

是

执行来源类型，参考执行来源类型枚举值说明

**∟** **sourceUuid**

**string**

是

执行来源uuid，一般等同于scheduleUuid


### 响应体示例

status可用于停止轮询的标识，当状态终态时，需要停止轮询,参考应用运行状态枚举值说明

**如遇到错误，请跳转到****状态码说明**


================================================================================
## 文档路径: 开放API/API接口/任务/最新任务执行记录
================================================================================

# 最新任务执行记录
路径: 开放API/API接口/任务/最新任务执行记录


# 最新任务执行记录


## 前置操作

​ **说明：该接口用户获取每条任务最新执行记录**

**说明：** 该接口是可以指定参数筛选查询本企业下的所有机器人，返回字段包括状态，名称，以及机器人所在终端的信息，可用作构建机器人管理模块，也可用于筛选空闲的机器人派发任务


## 模板


### postMan模板


### Java模板

​ 请求模型：

​ 响应模型：

​ 应用运行结果模型：


## 请求

|  |
|  |
| **HTTP URL** | **https://api.winrobot360.com/oapi/dispatch/v2/task/newest/list** | 专有云企业请使用专有云地址 |
| **HTTP Method** | **POST** |  |

**基本**


**说明**

**HTTP URL**

**https://api.winrobot360.com/oapi/dispatch/v2/task/newest/list**

专有云企业请使用专有云地址

**HTTP Method**

**POST**



### 请求头

|  |
|  |
| **Authorization** | **Bearer {accessToken}** | **{accessToken}变量需要替换成鉴权接口返回的access Token** |
| **Content-Type** | **application/json** |  |

**基本**


**说明**

**Authorization**

**Bearer {accessToken}**

**{accessToken}变量需要替换成鉴权接口返回的access Token**

**Content-Type**

**application/json**



### 请求体

|  |
|  |
| **statusList** | array | 任务运行状态集合 | 否 | 参考任务运行状态说明 |
|  | string | 子项 | 否 |  |
| **startTime** | string | 开始时间 | 否 | 开始时间，以触发时间进行查询 |
| **endTime** | string | 结束时间 | 否 | 结束时间，以触发时间进行查询 |
| **page** | number | 页码 | 否 | 分页参数，第几页 默认1 |
| **size** | number | 页数 | 否 | 分页参数一页多少条，默认20 |

**名称**

**类型**

**说明**

**是否必填**

**描述**

**statusList**

array

任务运行状态集合

否

参考任务运行状态说明


string

子项

否


**startTime**

string

开始时间

否

开始时间，以触发时间进行查询

**endTime**

string

结束时间

否

结束时间，以触发时间进行查询

**page**

number

页码

否

分页参数，第几页 默认1

**size**

number

页数

否

分页参数一页多少条，默认20


### 请求示例


```None
{
  "startTime":"2022-05-15 17:50:00",
  "endTime":"2022-05-16 17:50:00",
  "statusList":["finish","running"],
  "page":1,
  "size": 20
}
```


## 响应


### 响应体

|  |
|  |
| **code** | **int** | 是 | 状态码 200表示成功，非200表示失败 参考：**状态码说明** |
| **success** | **boolean** | 是 | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **msg** | **string** | 是 | 状态码描述 |
| **data** | **object** | 是 | 响应数据 |
| **∟** **cursorDirection** | **string** | 是 | 游标方向，next表明下一页，pre表示上一页 |
| **∟** **hasData** | **boolean** | 是 | 是否还有下一页数据 |
| **∟** **nextId** | **number** | 是 | 下一页id |
| **∟** **preId** | **number** | 是 | 上一页id |
| **∟** **dataList** | **array** | 是 | 任务所关联的应用运行信息，多个应用有多条 |
| **∟** **createTime** | **string** | 是 | 创建时间 格式yyyy-mm-dd hh:MM:dd |
| **∟ updateTime** | **string** | 是 | 更新时间 格式yyyy-mm-dd hh:MM:dd |
| **∟ executeScope** | **string** | 是 | 机器人执行策略 参考机器人执行策略枚举说明 |
| **∟ clientScope** | string |  |  |
| **∟ taskName** | **string** | 是 | 任务运行名称，等同于taskName |
| **∟** **id** | **numer** | 是 | 任务运行id |
| **∟** **sourceUuid** | **string** | 是 | 来源uuid，一般是scheduleUuid |
| **∟** **sourceType** | **string** | 是 | 来源类型，参考执行来源枚举说明 |
| **∟** **status** | **string** | 是 | 任务运行状态，该字段可以判断任务是否终态，终态时需要停止轮询该接口，参考任务运行状态枚举说明 |
| **∟ taskUuid** | **string** | 是 | 任务运行uuid 等同于sceneInstUuid |
| **∟** **userUuid** | **string** | 是 | 触发用户uuid |
| **∟** **userName** | **string** | 否 | 触发用户名称 |
| **∟** **runTimes** | **number** | 是 | 任务运行次数 |
| **∟ taskClients** | **array** | 是 | 等同sceneInstClients |
| **∟** **clientIp** | **string** | 否 | 机器人客户端ip |
| **∟ robotClientStatus** | **string** | 是 | 机器人状态 参考机器人状态枚举说明 |
| **∟** **currentRobotUuid** | **string** | 否 | 当前运行引用uuid |
| **∟ currentRobotName** | **string** | 否 | 当前运行应用名称 |
| **∟ description** | **string** | 否 | 机器人备注名称 |
| **∟ robotClientName** | **string** | 否 | 机器人名称 |
| **∟ robotClientUuid** | **string** | 否 | 机器人uuid |

**名称**

**类型**

**是否必填**

**描述**

**code**

**int**

是

状态码 200表示成功，非200表示失败 参考：**状态码说明**

**success**

**boolean**

是

调用是否成功，可以根据该字段判断接口调用是否成功

**msg**

**string**

是

状态码描述

**data**

**object**

是

响应数据

**∟** **cursorDirection**

**string**

是

游标方向，next表明下一页，pre表示上一页

**∟** **hasData**

**boolean**

是

是否还有下一页数据

**∟** **nextId**

**number**

是

下一页id

**∟** **preId**

**number**

是

上一页id

**∟** **dataList**

**array**

是

任务所关联的应用运行信息，多个应用有多条

**∟** **createTime**

**string**

是

创建时间 格式yyyy-mm-dd hh:MM:dd

**∟ updateTime**

**string**

是

更新时间 格式yyyy-mm-dd hh:MM:dd

**∟ executeScope**

**string**

是

机器人执行策略 参考机器人执行策略枚举说明

**∟ clientScope**

string



**∟ taskName**

**string**

是

任务运行名称，等同于taskName

**∟** **id**

**numer**

是

任务运行id

**∟** **sourceUuid**

**string**

是

来源uuid，一般是scheduleUuid

**∟** **sourceType**

**string**

是

来源类型，参考执行来源枚举说明

**∟** **status**

**string**

是

任务运行状态，该字段可以判断任务是否终态，终态时需要停止轮询该接口，参考任务运行状态枚举说明

**∟ taskUuid**

**string**

是

任务运行uuid 等同于sceneInstUuid

**∟** **userUuid**

**string**

是

触发用户uuid

**∟** **userName**

**string**

否

触发用户名称

**∟** **runTimes**

**number**

是

任务运行次数

**∟ taskClients**

**array**

是

等同sceneInstClients

**∟** **clientIp**

**string**

否

机器人客户端ip

**∟ robotClientStatus**

**string**

是

机器人状态 参考机器人状态枚举说明

**∟** **currentRobotUuid**

**string**

否

当前运行引用uuid

**∟ currentRobotName**

**string**

否

当前运行应用名称

**∟ description**

**string**

否

机器人备注名称

**∟ robotClientName**

**string**

否

机器人名称

**∟ robotClientUuid**

**string**

否

机器人uuid


### 响应体示例


```None

```


**如遇到错误，请跳转到****状态码说明**


================================================================================
## 文档路径: 开放API/API接口/任务/单个任务执行记录列表
================================================================================

# 单个任务执行记录列表
路径: 开放API/API接口/任务/单个任务执行记录列表


# 单个任务执行记录列表


## 前置操作

​ **1.通过****任务列表****接口获取scheduleUuid作为sourceUuid传入**

**说明：** 该接口获取单个任务执行记录，可用作点击具体某个任务时，展示任务下所有的执行记录(以瀑布流展示)，不支持跳页


## 模板


### postMan模板


### Java模板

​ 请求模型：

​ 响应模型：

​ 应用运行结果模型：


## 请求

|  |
|  |
| **HTTP URL** | **https://api.winrobot360.com/oapi/dispatch/v2/task/list** | 专有云企业请使用专有云地址 |
| **HTTP Method** | **POST** |  |

**基本**


**说明**

**HTTP URL**

**https://api.winrobot360.com/oapi/dispatch/v2/task/list**

专有云企业请使用专有云地址

**HTTP Method**

**POST**



### 请求头

|  |
|  |
| **Authorization** | **Bearer {accessToken}** | **{accessToken}变量需要替换成鉴权接口返回的access Token** |
| **Content-Type** | **application/json** |  |

**基本**


**说明**

**Authorization**

**Bearer {accessToken}**

**{accessToken}变量需要替换成鉴权接口返回的access Token**

**Content-Type**

**application/json**



### 请求体

|  |
|  |
| **sourceUuid** | string | 来源uuid | 是 | 任务uuid |  |
| **statusList** | array | 任务运行状态集合 | 否 | 参考任务运行状态说明 |  |
|  | string | 子项 | 否 |  |  |
| **startTime** | string | 开始时间 | 否 | 开始时间，以触发时间进行查询 |  |
| **endTime** | string | 结束时间 | 否 | 结束时间，以触发时间进行查询 |  |
| **cursorId** | number | 分页游标id | 否 | 第一页时不用传，点击下一页时需要传，该值取上一页最后一条记录id即可,点击上一页时，该值取上一次请求记录的第一条记录id即可 |  |
| **cursorDirection** | string | 分页方向 | 是 | pre:点击上一页，next:点击下一页，第一页时默认next |  |
| **size** | number | 每页多少条 | 是 | number | 每页多少 |

**名称**

**类型**

**说明**

**是否必填**

**描述**


**sourceUuid**

string

来源uuid

是

任务uuid


**statusList**

array

任务运行状态集合

否

参考任务运行状态说明



string

子项

否



**startTime**

string

开始时间

否

开始时间，以触发时间进行查询


**endTime**

string

结束时间

否

结束时间，以触发时间进行查询


**cursorId**

number

分页游标id

否

第一页时不用传，点击下一页时需要传，该值取上一页最后一条记录id即可,点击上一页时，该值取上一次请求记录的第一条记录id即可


**cursorDirection**

string

分页方向

是

pre:点击上一页，next:点击下一页，第一页时默认next


**size**

number

每页多少条

是

number

每页多少


### 请求示例


```None
{
  "sourceUuid":"4d8aae66-cec5-4043-85cc-70f4e0430111",
  "startTime":"2022-05-15 17:50:00",
  "endTime":"2022-05-16 17:50:00",
  "statusList":["finish","running"],
  "cursorId":123,
  "cursorDirection": "next",
  "size":10
}
```


## 响应


### 响应体

|  |
|  |
| **code** | **int** | 是 | 状态码 200表示成功，非200表示失败 参考：**状态码说明** |
| **success** | **boolean** | 是 | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **msg** | **string** | 是 | 状态码描述 |
| **data** | **object** | 是 | 响应数据 |
| **∟** **cursorDirection** | **string** | 是 | 游标方向，next表明下一页，pre表示上一页 |
| **∟** **hasData** | **boolean** | 是 | 是否还有下一页数据 |
| **∟** **nextId** | **number** | 是 | 下一页id |
| **∟** **preId** | **number** | 是 | 上一页id |
| **∟** **dataList** | **array** | 是 | 任务所关联的应用运行信息，多个应用有多条 |
| **∟** **createTime** | **string** | 是 | 创建时间 格式yyyy-mm-dd hh:MM:dd |
| **∟ updateTime** | **string** | 是 | 更新时间 格式yyyy-mm-dd hh:MM:dd |
| **∟ executeScope** | **string** | 是 | 机器人执行策略 参考机器人执行策略枚举说明 |
| **∟ clientScope** | string |  |  |
| **∟ taskName** | **string** | 是 | 任务运行名称，等同于taskName |
| **∟** **id** | **numer** | 是 | 任务运行id |
| **∟** **sourceUuid** | **string** | 是 | 来源uuid，一般是scheduleUuid |
| **∟** **sourceType** | **string** | 是 | 来源类型，参考执行来源枚举说明 |
| **∟** **status** | **string** | 是 | 任务运行状态，该字段可以判断任务是否终态，终态时需要停止轮询该接口，参考任务运行状态枚举说明 |
| **∟ taskUuid** | **string** | 是 | 任务运行uuid 等同于sceneInstUuid |
| **∟** **userUuid** | **string** | 是 | 触发用户uuid |
| **∟** **userName** | **string** | 否 | 触发用户名称 |
| **∟** **runTimes** | **number** | 是 | 任务运行次数 |
| **∟ taskClients** | **array** | 是 | 等同sceneInstClients |
| **∟** **clientIp** | **string** | 否 | 机器人客户端ip |
| **∟ robotClientStatus** | **string** | 是 | 机器人状态 参考机器人状态枚举说明 |
| **∟** **currentRobotUuid** | **string** | 否 | 当前运行引用uuid |
| **∟ currentRobotName** | **string** | 否 | 当前运行应用名称 |
| **∟** **description** | **string** | 否 | 机器人备注名称 |
| **∟** **robotClientName** | **string** | 否 | 机器人名称 |
| **∟** **robotClientUuid** | **string** | 否 | 机器人uuid |

**名称**

**类型**

**是否必填**

**描述**

**code**

**int**

是

状态码 200表示成功，非200表示失败 参考：**状态码说明**

**success**

**boolean**

是

调用是否成功，可以根据该字段判断接口调用是否成功

**msg**

**string**

是

状态码描述

**data**

**object**

是

响应数据

**∟** **cursorDirection**

**string**

是

游标方向，next表明下一页，pre表示上一页

**∟** **hasData**

**boolean**

是

是否还有下一页数据

**∟** **nextId**

**number**

是

下一页id

**∟** **preId**

**number**

是

上一页id

**∟** **dataList**

**array**

是

任务所关联的应用运行信息，多个应用有多条

**∟** **createTime**

**string**

是

创建时间 格式yyyy-mm-dd hh:MM:dd

**∟ updateTime**

**string**

是

更新时间 格式yyyy-mm-dd hh:MM:dd

**∟ executeScope**

**string**

是

机器人执行策略 参考机器人执行策略枚举说明

**∟ clientScope**

string



**∟ taskName**

**string**

是

任务运行名称，等同于taskName

**∟** **id**

**numer**

是

任务运行id

**∟** **sourceUuid**

**string**

是

来源uuid，一般是scheduleUuid

**∟** **sourceType**

**string**

是

来源类型，参考执行来源枚举说明

**∟** **status**

**string**

是

任务运行状态，该字段可以判断任务是否终态，终态时需要停止轮询该接口，参考任务运行状态枚举说明

**∟ taskUuid**

**string**

是

任务运行uuid 等同于sceneInstUuid

**∟** **userUuid**

**string**

是

触发用户uuid

**∟** **userName**

**string**

否

触发用户名称

**∟** **runTimes**

**number**

是

任务运行次数

**∟ taskClients**

**array**

是

等同sceneInstClients

**∟** **clientIp**

**string**

否

机器人客户端ip

**∟ robotClientStatus**

**string**

是

机器人状态 参考机器人状态枚举说明

**∟** **currentRobotUuid**

**string**

否

当前运行引用uuid

**∟ currentRobotName**

**string**

否

当前运行应用名称

**∟** **description**

**string**

否

机器人备注名称

**∟** **robotClientName**

**string**

否

机器人名称

**∟** **robotClientUuid**

**string**

否

机器人uuid



### 响应体示例

status可用于停止轮询的标识，当状态终态时，需要停止轮询,参考应用运行状态枚举值说明

**如遇到错误，请跳转到****状态码说明**


================================================================================
## 文档路径: 开放API/API接口/任务/查询任务详情
================================================================================

# 查询任务详情
路径: 开放API/API接口/任务/查询任务详情


# **查询任务详情**


## **前置操作**

​ **需要使用****鉴权接口****获取accessToken后，填写到对应的hearder中**

**说明：** 该接口查询单个任务详情，适用于用户自己建设任务管理功能


## **模板**


### **postMan模板**


### **Java模板**

​ 请求模型：

​ 响应模型：

​ 应用运行结果模型：



## 请求

| **基本** |  | 说明 |
| --- | --- | --- |
| **HTTP URL** | **https://api.winrobot360.com/oapi/dispatch/v2/schedule/detail** | 专有云企业请使用专有云地址 |
| **HTTP Method** | **POST** |  |

**基本**


说明

**HTTP URL**

**https://api.winrobot360.com/oapi/dispatch/v2/schedule/detail**

专有云企业请使用专有云地址

**HTTP Method**

**POST**



### **请求头**

|  |
|  |
| **Authorization** | **Bearer {accessToken}** | **{accessToken}变量需要替换成鉴权接口返回的access Token** |
| **Content-Type** | **application/json** |  |

**基本**


**说明**

**Authorization**

**Bearer {accessToken}**

**{accessToken}变量需要替换成鉴权接口返回的access Token**

**Content-Type**

**application/json**



### **请求体**

|  |
|  |
| **scheduleUuid** | **string** | 任务uuid | 是 | 从任务列表接口中获取 |

**名称**

**类型**

**说明**

**是否必填**

**描述**

**scheduleUuid**

**string**

任务uuid

是

从任务列表接口中获取


### **请求示例**


```None
{
  "scheduleUuid":"xxx"
}
```



## **响应**


### **响应体**

|  |
|  |
| **code** | **int** | **是** | **状态码 200表示成功，非200表示失败 参考：****状态码说明** |
| **success** | **boolean** | **是** | **调用是否成功，可以根据该字段判断接口调用是否成功** |
| **msg** | **string** | **是** | **状态码描述** |
| **data** | **object** | **是** | **响应数据** |
| **∟** **scheduleUuid** | **string** | **是** | **任务uuid** |
| **∟** **scheduleName** | **string** | **是** | **任务名称** |
| **∟** **scheduleType** | **string** | **是** | **任务类型，参考**任务类型枚举值说明 |
| **∟** **createTime** | **string** | **是** | **创建时间 yyyy-mm-dd hh:MM:ss** |
| **∟** **updateTime** | **string** | **是** | **更新时间 yyyy-mm-dd hh:MM:ss** |
| **∟** **clientScope** | **string** | **是** | **机器人选择范围 参考**机器人选择范围 |
| **∟** **executeScope** | **string** | **是** | **机器人执行策略** 机器人执行策略枚举说明 |
| **∟** **creatorName** | **string** | **是** | **创建人账号** |
| **∟** **creatorUuid** | **string** | **是** | **创建人uuid** |
| **∟** **modifierName** | **string** | **是** | **修改人账号** |
| **∟** **modifierUuid** | **string** | **是** | **修改人uuid** |
| **∟** **enabled** | **boolean** | **是** | **是否启用任务** |
| **∟** **enabledWaitTimeout** | **boolean** | **是** | **是否开启等待超时设置** |
| **∟** **errorProcess** | **string** | **是** | **错误执行策略** 异常执行策略枚举说明 |
| **∟ newestTaskUuid** | **string** | **否** | **最新一次任务运行uuid** |
| **∟** **runTimes** | **number** | **是** | **运行时间** |
| **∟** **priority** | **number** | **是** | **运行次数** |
| **∟ robotClientList** | **array** | **是** | **机器人相关** |
| **∟** **uuid** | **string**** ** | **是** | **机器人uuid** |
| **∟** **robotClientName** | **string** | **否** | **机器人名称，机器人被删除时为空** |
| **∟ robotList** | **array** | **是** | **应用相关** |
| **∟** **enableRunTimeout** | **boolean** | **是** | **是否开启应用运行超时设置** |
| **∟** **icon** | **string** | **否** | **图标** |
| **∟** **robotName** | **string** | **否** | **应用名称，如果应用被删除，为空** |
| **∟** **robotUuid** | **string** | **否** | **应用uuid，如果应用被删除，为空** |
| **∟** **supportParam** | **boolean** | **是** | **是否支持应用参数** |
| **∟ settings** | **array** | **否** | 当 **supportParam = true时，** 应用主流程参数不为空 |
| **∟ inputs** | **array** | 否 | 应用主流程输入参数 |
| **∟** **description** | **string** | 否 | 参数描述 |
| **∟** **direction** | **string** | 否 | 参数方向 Out为出参，In为入参 |
| **∟** **name** | **string** | 否 | 参数名称 |
| **∟** **type** | **string** | 否 | 参数类型，参考应用类型参数类型枚举值说明 |
| **∟** **value** | **string** | 否 | 参数值 |
| **∟ outputs** | **array** | 否 | 应用主流程输出参数 |
| **∟** **description** | **string** | 否 | 参数描述 |
| **∟** **direction** | **string** | 否 | 参数方向 Out为出参，In为入参 |
| **∟** **name** | **string** | 否 | 参数名称 |
| **∟** **type** | **string** | 否 | 参数类型，参考应用类型参数类型枚举值说明 |
| **∟** **value** | **string** | 否 | 参数值 |
| **∟ userGrantList** | **array** | **是** | **任务用户授权相关** |
| **∟** **userUuid** | **string** | **是** | **用户uuid** |
| **∟** **accountName** | **string** | **是** | **用户账号名称** |
| **∟** **waitTimeoutSettings** | **object** | **是** | **超时设置** |
| **∟** **waitTimeoutDay** | **number** | **否** | **等待超时 以天为单位** |
| **∟** **waitTimeoutHour** | **number** | **否** | **等待超时 以小时为单位** |
| **∟** **waitTimeoutMin** | **number** | **否** | **等待超时 以分钟为单位** |
| **∟** **cronInterface** | **object** | **是** | **定时器** |
| **∟** **cronExpress** | **string** | **否** | **cron表达式** |
| **∟** **dayOfWeeks** | **number** | **否** | **周天** |
| **∟** **hour** | **number** | **否** | **小时** |
| **∟** **minimumIntervalSeconds** | **number** | **否** | **最小秒** |
| **∟** **minute** | **numner** | **否** | **分钟** |
| **∟** **month** | **numner** | **否** | **月** |
| **∟** **nextTime** | **string** | **否** | **下一次触发时间 yyyy-mm-dd hh:MM:ss** |
| **∟** **time** | **string** | **否** | **时分秒** |
| **∟ type** | **string** | **否** | **定时器类型 参考**定时器类型枚举说明 |

**名称**

**类型**

**是否必填**

**描述**

**code**

**int**

**是**

**状态码 200表示成功，非200表示失败 参考：****状态码说明**

**success**

**boolean**

**是**

**调用是否成功，可以根据该字段判断接口调用是否成功**

**msg**

**string**

**是**

**状态码描述**

**data**

**object**

**是**

**响应数据**

**∟** **scheduleUuid**

**string**

**是**

**任务uuid**

**∟** **scheduleName**

**string**

**是**

**任务名称**

**∟** **scheduleType**

**string**

**是**

**任务类型，参考**任务类型枚举值说明

**∟** **createTime**

**string**

**是**

**创建时间 yyyy-mm-dd hh:MM:ss**

**∟** **updateTime**

**string**

**是**

**更新时间 yyyy-mm-dd hh:MM:ss**

**∟** **clientScope**

**string**

**是**

**机器人选择范围 参考**机器人选择范围

**∟** **executeScope**

**string**

**是**

**机器人执行策略** 机器人执行策略枚举说明

**∟** **creatorName**

**string**

**是**

**创建人账号**

**∟** **creatorUuid**

**string**

**是**

**创建人uuid**

**∟** **modifierName**

**string**

**是**

**修改人账号**

**∟** **modifierUuid**

**string**

**是**

**修改人uuid**

**∟** **enabled**

**boolean**

**是**

**是否启用任务**

**∟** **enabledWaitTimeout**

**boolean**

**是**

**是否开启等待超时设置**

**∟** **errorProcess**

**string**

**是**

**错误执行策略** 异常执行策略枚举说明

**∟ newestTaskUuid**

**string**

**否**

**最新一次任务运行uuid**

**∟** **runTimes**

**number**

**是**

**运行时间**

**∟** **priority**

**number**

**是**

**运行次数**

**∟ robotClientList**

**array**

**是**

**机器人相关**

**∟** **uuid**

**string**** **

**是**

**机器人uuid**

**∟** **robotClientName**

**string**

**否**

**机器人名称，机器人被删除时为空**

**∟ robotList**

**array**

**是**

**应用相关**

**∟** **enableRunTimeout**

**boolean**

**是**

**是否开启应用运行超时设置**

**∟** **icon**

**string**

**否**

**图标**

**∟** **robotName**

**string**

**否**

**应用名称，如果应用被删除，为空**

**∟** **robotUuid**

**string**

**否**

**应用uuid，如果应用被删除，为空**

**∟** **supportParam**

**boolean**

**是**

**是否支持应用参数**

**∟ settings**

**array**

**否**

当 **supportParam = true时，** 应用主流程参数不为空

**∟ inputs**

**array**

否

应用主流程输入参数

**∟** **description**

**string**

否

参数描述

**∟** **direction**

**string**

否

参数方向 Out为出参，In为入参

**∟** **name**

**string**

否

参数名称

**∟** **type**

**string**

否

参数类型，参考应用类型参数类型枚举值说明

**∟** **value**

**string**

否

参数值

**∟ outputs**

**array**

否

应用主流程输出参数

**∟** **description**

**string**

否

参数描述

**∟** **direction**

**string**

否

参数方向 Out为出参，In为入参

**∟** **name**

**string**

否

参数名称

**∟** **type**

**string**

否

参数类型，参考应用类型参数类型枚举值说明

**∟** **value**

**string**

否

参数值

**∟ userGrantList**

**array**

**是**

**任务用户授权相关**

**∟** **userUuid**

**string**

**是**

**用户uuid**

**∟** **accountName**

**string**

**是**

**用户账号名称**

**∟** **waitTimeoutSettings**

**object**

**是**

**超时设置**

**∟** **waitTimeoutDay**

**number**

**否**

**等待超时 以天为单位**

**∟** **waitTimeoutHour**

**number**

**否**

**等待超时 以小时为单位**

**∟** **waitTimeoutMin**

**number**

**否**

**等待超时 以分钟为单位**

**∟** **cronInterface**

**object**

**是**

**定时器**

**∟** **cronExpress**

**string**

**否**

**cron表达式**

**∟** **dayOfWeeks**

**number**

**否**

**周天**

**∟** **hour**

**number**

**否**

**小时**

**∟** **minimumIntervalSeconds**

**number**

**否**

**最小秒**

**∟** **minute**

**numner**

**否**

**分钟**

**∟** **month**

**numner**

**否**

**月**

**∟** **nextTime**

**string**

**否**

**下一次触发时间 yyyy-mm-dd hh:MM:ss**

**∟** **time**

**string**

**否**

**时分秒**

**∟ type**

**string**

**否**

**定时器类型 参考**定时器类型枚举说明





### **响应体示例**


**如遇到错误，请跳转到****状态码说明**


================================================================================
## 文档路径: 开放API/API接口/任务/查询任务列表
================================================================================

# 查询任务列表
路径: 开放API/API接口/任务/查询任务列表


# 查询任务列表


## 前置操作

​ **需要使用****鉴权接口****获取accessToken后，填写到对应的hearder中**

**说明：** 该接口查询租户下所有任务列表，适用场景用于建立自己的任务管理功能


## 模板


### postMan模板


### Java模板

​ 请求模型：

​ 响应模型：

​ 应用运行结果模型：


## 请求

|  |
|  |
| **HTTP URL** | **https://api.winrobot360.com/oapi/dispatch/v2/schedule/list** | 专有云企业请使用专有云地址 |
| **HTTP Method** | **POST** |  |

**基本**


**说明**

**HTTP URL**

**https://api.winrobot360.com/oapi/dispatch/v2/schedule/list**

专有云企业请使用专有云地址

**HTTP Method**

**POST**




### 请求头

|  |
|  |
| **Authorization** | **Bearer {accessToken}** | **{accessToken}变量需要替换成鉴权接口返回的access Token** |
| **Content-Type** | **application/json** |  |

**基本**


**说明**

**Authorization**

**Bearer {accessToken}**

**{accessToken}变量需要替换成鉴权接口返回的access Token**

**Content-Type**

**application/json**




### 请求体

|  |
|  |
| **key** | **string** | 搜索关键字 | 否 | 目前作用在任务名称模糊搜索 |
| **enabled** | **boolean** | 是否启用 | 否 | 是否启用任务 |
| **scheduleType** | **string** | 任务类型 | 否 | 参考任务类型枚举值说明 |
| **page** | **number** | 分页之第几页 | 否 | 默认值1，从第一页开始 |
| **size** | **number** | 分页之每页多少条 | 否 | 默认值20，一页20条，该值最高上限500 |

**名称**

**类型**

**说明**

**是否必填**

**描述**

**key**

**string**

搜索关键字

否

目前作用在任务名称模糊搜索

**enabled**

**boolean**

是否启用

否

是否启用任务

**scheduleType**

**string**

任务类型

否

参考任务类型枚举值说明

**page**

**number**

分页之第几页

否

默认值1，从第一页开始

**size**

**number**

分页之每页多少条

否

默认值20，一页20条，该值最高上限500


### 请求示例


```None
{
  "key":"测试",
  "enabled":true,
  "type":"period",
  "page":1,
  "size":10,
  "orderBy":"createTime"
}
```


## 响应


### 响应体

|  |
|  |
| **code** | **int** | 是 | 状态码 200表示成功，非200表示失败 参考：**状态码说明** |
| **success** | **boolean** | 是 | 调用是否成功，可以根据该字段判断接口调用是否成功 |
| **msg** | **string** | 是 | 状态码描述 |
| **data** | **object** | 是 | 响应数据 |
| **∟** **scheduleUuid** | **string** | 是 | 任务uuid |
| **∟** **scheduleName** | **string** | 是 | 任务名称 |
| **∟** **scheduleType** | **string** | 是 | 任务类型，参考任务类型枚举值说明 |
| **∟** **createTime** | **string** | 是 | 创建时间 yyyy-mm-dd hh:MM:ss |
| **∟** **updateTime** | **string** | 是 | 更新时间 yyyy-mm-dd hh:MM:ss |
| **∟** **cronInterface** | **object** | 是 | 定时器 |
| **∟** **cronExpress** | **string** | 否 | cron表达式 |
| **∟** **dayOfWeeks** | **number** | 否 | 周天 |
| **∟** **hour** | **number** | 否 | 小时 |
| **∟** **minimumIntervalSeconds** | **number** | 否 | 最小秒 |
| **∟** **minute** | **numner** | 否 | 分钟 |
| **∟** **month** | **numner** | 否 | 月 |
| **∟** **nextTime** | **string** | 否 | 下一次触发时间 yyyy-mm-dd hh:MM:ss |
| **∟** **time** | **string** | 否 | 时分秒 |
| **∟ type** | **string** | 否 | 定时器类型 参考定时器类型枚举说明 |

**名称**

**类型**

**是否必填**

**描述**

**code**

**int**

是

状态码 200表示成功，非200表示失败 参考：**状态码说明**

**success**

**boolean**

是

调用是否成功，可以根据该字段判断接口调用是否成功

**msg**

**string**

是

状态码描述

**data**

**object**

是

响应数据

**∟** **scheduleUuid**

**string**

是

任务uuid

**∟** **scheduleName**

**string**

是

任务名称

**∟** **scheduleType**

**string**

是

任务类型，参考任务类型枚举值说明

**∟** **createTime**

**string**

是

创建时间 yyyy-mm-dd hh:MM:ss

**∟** **updateTime**

**string**

是

更新时间 yyyy-mm-dd hh:MM:ss

**∟** **cronInterface**

**object**

是

定时器

**∟** **cronExpress**

**string**

否

cron表达式

**∟** **dayOfWeeks**

**number**

否

周天

**∟** **hour**

**number**

否

小时

**∟** **minimumIntervalSeconds**

**number**

否

最小秒

**∟** **minute**

**numner**

否

分钟

**∟** **month**

**numner**

否

月

**∟** **nextTime**

**string**

否

下一次触发时间 yyyy-mm-dd hh:MM:ss

**∟** **time**

**string**

否

时分秒

**∟ type**

**string**

否

定时器类型 参考定时器类型枚举说明



### 响应体示例


```None

```


**如遇到错误，请跳转到****状态码说明**


================================================================================
## 文档路径: 开放API/通用说明/限流说明
================================================================================

# 限流说明
路径: 开放API/通用说明/限流说明

为了保障服务性能和业务稳定，影刀开放 API 会针对不同用户发起的 API 请求设置相应的频率限制策略。当响应结果包含 `RequestLimitExceeded` 错误码时，说明当前请求已超出接口频率限制。此时，可以采用延迟重试等方法来避免限频错误。



## **接口的频率限制**

| 接口地址 | 频率限制（次/秒） |
| --- | --- |
| /oapi/dispatch/v2/job/query | 30 |
| /oapi/dispatch/v2/task/list | 10 |
| /oapi/dispatch/v2/task/process/detail | 10 |
| /oapi/dispatch/v2/client/query | 20 |
| /oapi/dispatch/v2/schedule/list | 10 |
| /oapi/dispatch/v2/job/stop | 10 |
| /oapi/dispatch/v2/task/query | 10 |
| /oapi/dispatch/v2/job/list | 10 |
| /oapi/dispatch/v2/job/log/search | 5 |
| /oapi/dispatch/v2/job/start | 10 |
| /oapi/dispatch/v2/client/list | 10 |
| /oapi/dispatch/v2/task/start | 10 |
| /oapi/dispatch/v2/schedule/detail | 10 |
| /oapi/dispatch/v2/task/stop | 10 |
| /oapi/dispatch/v2/job/retry | 10 |
| /oapi/dispatch/v2/job/log/notify | 5 |
| /oapi/dispatch/v2/job/log/query | 5 |
| /oapi/dispatch/v2/client/group/list | 5 |
| /oapi/dispatch/v2/file/upload | 5 |
| /oapi/dispatch/v2/task/newest/list | 5 |
| /oapi/app/open/market/addMarketUser | 5 |
| /oapi/app/open/marketchangeMarketUser | 5 |
| /oapi/app/open/market/dealApproval | 5 |
| /oapi/app/open/market/pageByMarketIdList | 5 |
| /oapi/app/open/market/listByMarketIdsAndUserId | 5 |
| /oapi/app/open/market/listMarketByMarketOwnerId | 5 |
| /oapi/app/open/market/batchSaveMarket | 5 |
| /oapi/app/open/market/deleteMarketApp | 5 |
| /oapi/app/open/translate/owner | 5 |
| /oapi/app/open/query/list | 5 |
| /oapi/app/open/query/use/record/list | 5 |
| /oapi/app/open/query/pageRunRecordData | 5 |
| /oapi/app/open/query/appVersionDetail | 5 |
| /oapi/app/open/query/appOnlineDetailWithParam | 5 |
| /oapi/app/open/historyVersionList | 5 |
| /oapi/robot/v2/query | 5 |
| /oapi/robot/v2/queryRobotParam | 3 |
| /oapi/resource/tag/save | 5 |
| /oapi/resource/tag/delete | 5 |
| /oapi/resource/tag/listByIds | 5 |
| /oapi/rpa/user/v1/list | 5 |
| /oapi/rpa/user/v1/create | 10 |
| /oapi/rpa/user/v1/modify | 5 |
| /oapi/rpa/user/v1/delete | 5 |
| /oapi/rpa/user/v1/createExtraRpaEnterpriseUser | 5 |
| /oapi/rpa/user/v1/delayExtraRpaEnterpriseUser | 5 |
| /oapi/rpa/user/v2/create | 10 |
| /oapi/token/v2/token/create | 20 |
| /oapi/token/v2/signature/create | 5 |
| /oapi/calendar/v1/save | 5 |
| /oapi/calendar/v1/delete | 5 |
| /oapi/calendar/v1/queryCalendarDetail | 5 |

接口地址

频率限制（次/秒）

/oapi/dispatch/v2/job/query

30

/oapi/dispatch/v2/task/list

10

/oapi/dispatch/v2/task/process/detail

10

/oapi/dispatch/v2/client/query

20

/oapi/dispatch/v2/schedule/list

10

/oapi/dispatch/v2/job/stop

10

/oapi/dispatch/v2/task/query

10

/oapi/dispatch/v2/job/list

10

/oapi/dispatch/v2/job/log/search

5

/oapi/dispatch/v2/job/start

10

/oapi/dispatch/v2/client/list

10

/oapi/dispatch/v2/task/start

10

/oapi/dispatch/v2/schedule/detail

10

/oapi/dispatch/v2/task/stop

10

/oapi/dispatch/v2/job/retry

10

/oapi/dispatch/v2/job/log/notify

5

/oapi/dispatch/v2/job/log/query

5

/oapi/dispatch/v2/client/group/list

5

/oapi/dispatch/v2/file/upload

5

/oapi/dispatch/v2/task/newest/list

5

/oapi/app/open/market/addMarketUser

5

/oapi/app/open/marketchangeMarketUser

5

/oapi/app/open/market/dealApproval

5

/oapi/app/open/market/pageByMarketIdList

5

/oapi/app/open/market/listByMarketIdsAndUserId

5

/oapi/app/open/market/listMarketByMarketOwnerId

5

/oapi/app/open/market/batchSaveMarket

5

/oapi/app/open/market/deleteMarketApp

5

/oapi/app/open/translate/owner

5

/oapi/app/open/query/list

5

/oapi/app/open/query/use/record/list

5

/oapi/app/open/query/pageRunRecordData

5

/oapi/app/open/query/appVersionDetail

5

/oapi/app/open/query/appOnlineDetailWithParam

5

/oapi/app/open/historyVersionList

5

/oapi/robot/v2/query

5

/oapi/robot/v2/queryRobotParam

3

/oapi/resource/tag/save

5

/oapi/resource/tag/delete

5

/oapi/resource/tag/listByIds

5

/oapi/rpa/user/v1/list

5

/oapi/rpa/user/v1/create

10

/oapi/rpa/user/v1/modify

5

/oapi/rpa/user/v1/delete

5

/oapi/rpa/user/v1/createExtraRpaEnterpriseUser

5

/oapi/rpa/user/v1/delayExtraRpaEnterpriseUser

5

/oapi/rpa/user/v2/create

10

/oapi/token/v2/token/create

20

/oapi/token/v2/signature/create

5

/oapi/calendar/v1/save

5

/oapi/calendar/v1/delete

5

/oapi/calendar/v1/queryCalendarDetail

5



## **提高接口的频率限制**

当接口默认频率限制不满足业务实际需求时，您可以联系客服申请提高限频。我们将综合系统资源、稳定性等因素全面评估申请，评估通过后将会为您调整限频。


================================================================================
## 文档路径: 开放API/通用说明/常见问题说明
================================================================================

# 常见问题说明
路径: 开放API/通用说明/常见问题说明


# 常见问题说明


### 1.专有云无法使用调度API


#### 描述

调用job/start或者task/start不成功


#### 原因及解决方案


##### 原因一

未使用专有云地址进行请求


##### 解决方案

使用专有云地址进行请求


##### 原因二

key和secret不对，可能在使用公有云的key和secret


##### 解决方案

根据提示调整key和secret


##### 原因三

用户可能直接copy了接口注释


##### 解决方案

postMan不会识别注释，直接去掉注释即可


### 2.无法使用job/stop，job/query，task/stop，task/query相关查询和控制接口


#### 描述

目前接口存在两个维度，一个是task/xxx,一个是job/xxx, job/xxx提供一些简单1:1指定机器人和应用运行方式，不提供编排能力，task是需要在控制台新建任务，通过任务uuid指定启动，提供更丰富的能力


#### 原因及解决方案


##### 原因一

接口信息传入错误，比如job/stop. 却使用taskUuid进行停止


##### 解决方案

使用正确的参数，jobUuid和taskUuid分别由job/start,task/start接口返回


### 3.调度api用户没收到回调


#### 描述

场景1：job/start，应用运行完成没收到回调 场景2: task/start, 任务下所有应用运行完成没收到回调


#### 可能原因及解决方案


##### 原因一

客户端显示应用运行完成，但是服务端还是显示应用运行中(api执行记录)


##### 解决方案

1.属于任务状态不同步，请参考三，目前只能先在控制台手动关闭任务，触发回调


##### 原因二

客户端和控制台均显示完成，但是客户没收到回调


##### 解决方案

1. 用户需要检查自己防火墙策略，确认自己白名单是否加上影刀线上服务器ip，线上ip地址需要咨询云山
2. 用户自己提供的接口是否可以被公网访问
3. 用户本地调试的接口，线上服务器不支持回调
4. api_callback_record表，根据sourceUuid进行查询

用户需要检查自己防火墙策略，确认自己白名单是否加上影刀线上服务器ip，线上ip地址需要咨询云山

用户自己提供的接口是否可以被公网访问

用户本地调试的接口，线上服务器不支持回调

api_callback_record表，根据sourceUuid进行查询

备注: 服务端有立即补偿和定时补偿，立即补偿会500ms，750ms, 1500ms间隔重试3次，定时补偿会每小时整点进行补偿，补偿24次


### 4.调度api指定了accountName,但是没有起效果


#### 描述

指定了ceshi1账号，但是由其他账号执行应用


#### 原因及解决方案


##### 原因一

同时填了accountName和robotClientGroupUuid，优先是以分组为准


##### 解决方案

去掉robotClientGroupUuid


### 5.调度API调用startJob接口时提示userUuid字段 must not null


#### 描述

使用之前旧的key和secret可能会出现

**示例：**


![None](https://xybot-oss-cdn.yingdao.com/yddoc/rpa_zh-CN/asset/710463081711124480/dcadd6ee-de5a-47f4-bbf8-02e96095b85b/img0.png)


#### 原因及解决方案


##### 原因一

创建key和secret的管理员用户被删除了


##### 如何解决

重新用管理员账号生成一个key和secret，替换一下

备注：之所以这样设计的原因，是因为执行记录要关联到代理用户身上(key和secret只是一个开发平台配置，非具体用户)，这个代理用户是指创建key和secret的用户


### 6.应用设置了主流程参数，但是控制台配置任务时，参数按钮还是置灰


#### 描述

应用设置了主流程参数，但是控制台配置任务时，参数按钮还是置灰


#### 原因及解决方案


##### 原因一

应用设置的主流程参数过大，超过4000字节会丢失，比如设置的默认值很长


##### 解决方案

1. 调整默认值
2. 长文本转成文件形式

调整默认值

长文本转成文件形式


### 7.通过调度task/start接口传入输入参数失败


#### 描述

执行记录-应用运行记录-没有执行参数

1. start/job接口有传入输入参数
2. 运行记录中执行参数为空

start/job接口有传入输入参数

运行记录中执行参数为空


#### 原因及解决方案


##### 原因一

应用设置的主流程参数过大，超过4000字节会丢失，比如设置的默认值很长


##### 解决方案

1. 调整默认值
2. 长文本转成文件形式

调整默认值

长文本转成文件形式


##### 原因二

主流程参数设置错误


##### 解决方案

1. 请下载模板，然后找到主流程参数的请求模板替换参数即可
2. 核对下robotUuid和新建任务选择的uuid是否一致
3. 因为一个任务是可以配置多个任务，所以scheduleRelaParams是一个对象集合，请参考以下截图

请下载模板，然后找到主流程参数的请求模板替换参数即可

核对下robotUuid和新建任务选择的uuid是否一致

因为一个任务是可以配置多个任务，所以scheduleRelaParams是一个对象集合，请参考以下截图



![None](https://xybot-oss-cdn.yingdao.com/yddoc/rpa_zh-CN/asset/710463081711124480/c798492c-1a86-4e96-8440-fcf16a4e3ce8/img1.png)


##### 原因三

前端遗留问题，需要新建/编辑任务时点击下参数按钮，保存


##### 解决方案

编辑任务，点击参数，保存


##### 原因四

应用被删除，或者未发版


### 8.通过调度job/start接口提示机器人账号不存在


#### 描述

job/start接口提示机器人账号不存在


#### 可能原因及解决方案


##### 原因一

没有传accountName或 robotClientGroupUuid


##### 解决方案

传入对应的机器人账号或执行机器人分组uuid，机器人分组uuid可从机器人管理 → 机器人分组 → 右键复制


##### 原因二

传入错误的accountName


##### 解决方案

1.检查控制台-机器人管理列表界面是否有对应的机器人


##### 原因三

传入正确的用户账号，但是该账号一次都没有切换成调度模式

新建账号必须要切换一次调度模式才能注册成为机器人，换句话说用户账号不等同于机器人


##### 解决方案

使用新建账号，登录客户端切换调度模式


### 9.是否只有调度模式下才能使用调度API


#### 描述

只有调度模式下，才可以使用接口启动job, 如果不是调度模式，使用接口启动job，并没有真正启动任务，接口也没有返回错误信息


#### 解决方案

有排队机制(默认10分钟)，排队等待时间枚举参考调度api文档枚举，即使机器人不在线，也能调用成功，只要10分钟期间机器人连上，就能执行队列中的任务，所以不会返回错误


### 10.回调接口是否需要验签

建议进行验签处理，避免恶意攻击


================================================================================
## 文档路径: 开放API/通用说明/应用主流程参数说明
================================================================================

# 应用主流程参数说明
路径: 开放API/通用说明/应用主流程参数说明


# 应用主流程参数说明

|  |
|  |
| **name** | string | 参数名 | 是 | 需与客户端主流程应用参数保持一致 |
| **value** | string | 参数值 | 是 |  |
| **type** | string | 参数类型 | 是 | 主流程参数类型，需要与客户端填写的主流程参数保持一致，参考枚举值说明 |

**名称**

**类型**

**说明**

**是否必填**

**描述**

**name**

string

参数名

是

需与客户端主流程应用参数保持一致

**value**

string

参数值

是


**type**

string

参数类型

是

主流程参数类型，需要与客户端填写的主流程参数保持一致，参考枚举值说明


================================================================================
## 文档路径: 开放API/通用说明/状态码说明
================================================================================

# 状态码说明
路径: 开放API/通用说明/状态码说明


# 状态码说明

|  |
|  |
| **200** | 正常 | 调用正常 |
| **401** | 接口未授权 | 1.排查accessKeyId和accessSecret配置项是否正确 2.排查请求地址是否填写正常， 公有云需要填写:https://console.yingdao.com/dispatch/monitoring/index 专有云填写对应专有云部署对应的地址 |
| **400** | 接口参数校验错误 | 1.未配置accessKeyId, 请用企业管理员登录后台并进行配置 2.未配置accessKeySecret, 请用企业管理员登录后台并进行配置3.accessKeyId错误，请用企业管理员登录后台并进行核对 4.accessKeySecret错误，请用企业管理员登录后台并进行核对!!! |
| **429** |  触发接口限流 | 请求频率过高触发接口限流，稍后重试。详情参考https://www.yingdao.com/yddoc/rpa/zh-CN/912893828744491008 |
| **500** | 服务内部错误 | 请联系技术支持进行排查 |

**错误码**

**说明**

**排查建议**

**200**

正常

调用正常

**401**

接口未授权

1.排查accessKeyId和accessSecret配置项是否正确 2.排查请求地址是否填写正常， 公有云需要填写:https://console.yingdao.com/dispatch/monitoring/index 专有云填写对应专有云部署对应的地址

**400**

接口参数校验错误

1.未配置accessKeyId, 请用企业管理员登录后台并进行配置 2.未配置accessKeySecret, 请用企业管理员登录后台并进行配置3.accessKeyId错误，请用企业管理员登录后台并进行核对 4.accessKeySecret错误，请用企业管理员登录后台并进行核对!!!

**429**

 触发接口限流

请求频率过高触发接口限流，稍后重试。详情参考https://www.yingdao.com/yddoc/rpa/zh-CN/912893828744491008

**500**

服务内部错误

请联系技术支持进行排查


================================================================================
## 文档路径: 开放API/通用说明/响应格式说明
================================================================================

# 响应格式说明
路径: 开放API/通用说明/响应格式说明


# 响应格式说明

|  |
|  |
| **code** | int | 是 | 3 | 状态码 | 0 |
| **msg** | String | 是 | - | 响应msg | accessKeyId错误，请用企业管理员登录后台并进行核对 |
| **success** | boolean | 是 | - | true/false,表示是否调用成功 | true |
| **requestId** | String | 否 | - | 请求id | 调用失败时 |
| **data** | Object | 是 | 响应数据 |  |  |

**参数**

**类型**

**是否必填**

**最大长度**

**描述**

**示例值**

**code**

int

是

3

状态码

0

**msg**

String

是

-

响应msg

accessKeyId错误，请用企业管理员登录后台并进行核对

**success**

boolean

是

-

true/false,表示是否调用成功

true

**requestId**

String

否

-

请求id

调用失败时

**data**

Object

是

响应数据





================================================================================
## 文档路径: 开放API/通用说明/枚举值说明
================================================================================

# 枚举值说明
路径: 开放API/通用说明/枚举值说明


# 枚举值说明


### 一.任务运行状态枚举说明(sceneInstStatus)

|  |
|  |
| **waiting** | 等待调度 | 否 |
| **running** | 任务运行中 | 否 |
| **finish** | 任务运行结束 | 是 |
| **stopping** | 任务正在停止 | 否 |
| **stopped** | 已结束 | 是 |
| **error** | 异常 | 是 |

**状态码**

**状态码描述**

**是否终态**

**waiting**

等待调度

否

**running**

任务运行中

否

**finish**

任务运行结束

是

**stopping**

任务正在停止

否

**stopped**

已结束

是

**error**

异常

是



### 二.应用运行状态枚举值说明(sceneInstJobStatus)

|  |
|  |
| **created** | 已创建 | 否 |
| **waiting** | 等待调度 | 否 |
| **running** | 运行中 | 否 |
| **finish** | 完成 | 是 |
| **stopping** | 停止中 | 否 |
| **stopped** | 已停止 | 是 |
| **error** | 异常 | 是 |
| **skipped** | 已跳过 | 是 |
| **cancel** | 已取消 | 是 |

**状态码**

**状态码描述**

**是否终态**

**created**

已创建

否

**waiting**

等待调度

否

**running**

运行中

否

**finish**

完成

是

**stopping**

停止中

否

**stopped**

已停止

是

**error**

异常

是

**skipped**

已跳过

是

**cancel**

已取消

是

终态可以作为是否停止轮询的唯一标识



### 三.等待超时时间枚举值说明

|  |
|  |
| **10m** | 10分钟 |
| **20m** | 20分钟 |
| **30m** | 30分钟 |
| **1h** | 1小时 |
| **2h** | 2小时 |

**状态码**

**状态码描述**

**10m**

10分钟

**20m**

20分钟

**30m**

30分钟

**1h**

1小时

**2h**

2小时



### 四.应用运行参数类型说明

|  |
|  |
| **str** | 字符串 |
| **int** | 整型 |
| **float** | 浮点 |
| **bool** | 布尔 |
| **file** | 文件 |

**状态码**

**状态码描述**

**str**

字符串

**int**

整型

**float**

浮点

**bool**

布尔

**file**

文件



### 五.应用运行优先级枚举值说明

|  |
|  |
| **high** | 高 |
| **middle** | 中 |
| **low** | 低 |

**状态码**

**状态码描述**

**high**

高

**middle**

中

**low**

低



### 六.机器人状态枚举值说明

|  |
|  |
| **connected** | 已连接 | 长链建立起来时的状态，该状态持续时间非常短 |
| **idle** | 空闲 | 该状态下，可以直接调度应用，不需要排队 |
| **allocated** | 已分配 | 表示机器人已被分配应用，但是机器人还未运行应用，该状态下继续分配应用，会进行排队，该状态持续时间较短 |
| **running** | 运行中 | 表示机器人正在运行应用，该状态下继续分配应用会进行排队 |
| **offline** | 离线 | 断网或者退出调度模式，退出客户端，机器人所处的状态，该状态下继续分配应用会进行排队 |

**状态码**

**状态码描述**

**描述**

**connected**

已连接

长链建立起来时的状态，该状态持续时间非常短

**idle**

空闲

该状态下，可以直接调度应用，不需要排队

**allocated**

已分配

表示机器人已被分配应用，但是机器人还未运行应用，该状态下继续分配应用，会进行排队，该状态持续时间较短

**running**

运行中

表示机器人正在运行应用，该状态下继续分配应用会进行排队

**offline**

离线

断网或者退出调度模式，退出客户端，机器人所处的状态，该状态下继续分配应用会进行排队



### 七.回调数据类型枚举值说明

|  |
|  |
| **job** | 应用运行数据回调 |
| **task** | 任务运行数据回调 |

**状态码**

**状态码描述**

**job**

应用运行数据回调

**task**

任务运行数据回调



### 八.执行范围枚举值说明

|  |
|  |
| **any** | 任意一台 |
| **all** | 全部执行 |

**状态码**

**状态码描述**

**any**

任意一台

**all**

全部执行



## 九.定时器类型枚举值说明

|  |
|  |
| **timer** | 定时模式，固定时间 |
| **minute** | 分钟 |
| **hour** | 小时 |
| **day** | 天 |
| **week** | 周 |
| **month** | 月 |
| **cron** | cron周期模式 |
| **manual** | 手动 |

**状态码**

**状态码描述**

**timer**

定时模式，固定时间

**minute**

分钟

**hour**

小时

**day**

天

**week**

周

**month**

月

**cron**

cron周期模式

**manual**

手动



## 十.机器人选择范围枚举值说明

|  |
|  |
| **assign** | 指定具体需要运行应用的机器人 |
| **group** | 通过指定机器人分组的方式来指定机器人范围 |

**状态码**

**状态码描述**

**assign**

指定具体需要运行应用的机器人

**group**

通过指定机器人分组的方式来指定机器人范围



## 十一.机器人执行策略枚举值说明

|  |
|  |
| **any** | 随机空闲机器人 |
| **all** | 全部机器人执行 |

**状态码**

**状态码描述**

**any**

随机空闲机器人

**all**

全部机器人执行



## 十二.异常执行策略枚举值说明

|  |
|  |
| **terminate** | 停止执行 |
| **continue** | 继续执行 |
| **retry** | 重试 |
| **retry_continue** | 重试-继续执行 |

**状态码**

**状态码描述**

**terminate**

停止执行

**continue**

继续执行

**retry**

重试

**retry_continue**

重试-继续执行



## 十三.执行来源枚举值说明

|  |
|  |
| **period** | 计划触发 |
| **timer** | 定时触发 |
| **manual** | 手动触发 |
| **api** | api触发 |

**状态码**

**状态码描述**

**period**

计划触发

**timer**

定时触发

**manual**

手动触发

**api**

api触发


================================================================================
## 文档路径: 开放API/参考案例下载
================================================================================

# 参考案例下载
路径: 开放API/参考案例下载


# 参考案例下载


#### postMan模板汇总

调度开放api汇总.json （右键另存为）


#### java模板汇总

xybot-api-sdk.rar （右键另存为）


#### python模板汇总

apiDispatch.py （右键另存为）


#### 回调签名验签

SignDemoNew.java（右键另存为）

**说明:**

accessKeyId：影刀控制台生成的accessKeyId，登录后选择API执行->API配置

bodyMd5：请求体加密的字符串，可由回调接口回传回去

timestamp：精确到秒的时间戳即可，由回调接口回传回去
