"""Swift declarations and deliberately file-local bindings; no package/build execution."""
from collections import defaultdict
import json

EXTENSIONS={'.swift':'swift'}
DEFINITIONS={'function_declaration':'function','class_declaration':'class','protocol_declaration':'interface'}
CALL_TYPES=('call_expression',)
BINARY={'additive_expression','multiplicative_expression','infix_expression','comparison_expression',
        'conjunction_expression','disjunction_expression','nil_coalescing_expression'}
# Top-level public/SPI SDK names, Apple Swift 6.4.2 symbol graph in mode 5; collision guard.
STDLIB_NAMES=set("""
Actor AdditiveArithmetic AnyActor AnyBidirectionalCollection AnyClass AnyCollection AnyHashable AnyIndex
AnyIterator AnyKeyPath AnyObject AnyRandomAccessCollection AnyRegexOutput AnySequence Array
ArrayLiteralConvertible ArraySlice AsyncCompactMapSequence AsyncDropFirstSequence AsyncDropWhileSequence
AsyncFilterSequence AsyncFlatMapSequence AsyncIteratorProtocol AsyncMapSequence AsyncPrefixSequence
AsyncPrefixWhileSequence AsyncSequence AsyncStream AsyncThrowingCompactMapSequence
AsyncThrowingDropWhileSequence AsyncThrowingFilterSequence AsyncThrowingFlatMapSequence
AsyncThrowingMapSequence AsyncThrowingPrefixWhileSequence AsyncThrowingStream
AutoreleasingUnsafeMutablePointer BidirectionalCollection BidirectionalIndexable BidirectionalSlice
BinaryFloatingPoint BinaryInteger BitwiseCopyable Bool BooleanLiteralConvertible BooleanLiteralType
BorrowingIteratorAdapter BorrowingIteratorProtocol ByteOrder CBool CChar CChar16 CChar32 CChar8 CDouble
CFloat CFloat16 CInt CLong CLongDouble CLongLong CShort CSignedChar CUnsignedChar CUnsignedInt
CUnsignedLong CUnsignedLongLong CUnsignedShort CVaListPointer CVarArg CWideChar CancellationError
CaseIterable Character CheckedContinuation Clock ClosedRange ClosedRangeIndex Codable CodingKey
CodingKeyRepresentable CodingUserInfoKey Collection CollectionDifference CollectionOfOne CommandLine
Comparable ConcurrentValue ContiguousArray Continuation ContinuousClock ConvertibleFromBytes
ConvertibleToBytes Copyable CountableClosedRange CountablePartialRangeFrom CountableRange
CustomConsumingRegexComponent CustomDebugStringConvertible CustomLeafReflectable
CustomPlaygroundDisplayConvertible CustomPlaygroundQuickLookable CustomReflectable
CustomStringConvertible DebugDescription Decodable Decoder DecodingError DefaultBidirectionalIndices
DefaultIndices DefaultRandomAccessIndices DefaultStringInterpolation Dictionary DictionaryIndex
DictionaryIterator DictionaryLiteral DictionaryLiteralConvertible DiscardingTaskGroup DiscontiguousSlice
Double DropFirstSequence DropWhileSequence Duration DurationProtocol EmptyCollection EmptyIterator
Encodable Encoder EncodingError EnumeratedIterator EnumeratedSequence Equatable Error Escapable Executor
ExecutorJob ExpressibleByArrayLiteral ExpressibleByBooleanLiteral ExpressibleByDictionaryLiteral
ExpressibleByExtendedGraphemeClusterLiteral ExpressibleByFloatLiteral ExpressibleByIntegerLiteral
ExpressibleByNilLiteral ExpressibleByStringInterpolation ExpressibleByStringLiteral
ExpressibleByUnicodeScalarLiteral ExtendedGraphemeClusterLiteralConvertible ExtendedGraphemeClusterType
FixedWidthInteger FlattenBidirectionalCollection FlattenBidirectionalCollectionIndex FlattenCollection
FlattenCollectionIndex FlattenSequence Float Float16 Float32 Float64 FloatLiteralConvertible
FloatLiteralType FloatingPoint FloatingPointClassification FloatingPointRoundingRule FloatingPointSign
FullyInhabited GlobalActor Hashable Hasher Identifiable Indexable IndexableBase IndexingIterator
InlineArray InstantProtocol Int Int128 Int16 Int32 Int64 Int8 IntegerLiteralConvertible
IntegerLiteralType Iterable IteratorOverOne IteratorProtocol IteratorSequence Job JobPriority
JoinedIterator JoinedSequence KeyPath KeyValuePairs KeyedDecodingContainer KeyedDecodingContainerProtocol
KeyedEncodingContainer KeyedEncodingContainerProtocol LazyBidirectionalCollection LazyCollection
LazyCollectionProtocol LazyDropWhileBidirectionalCollection LazyDropWhileCollection LazyDropWhileIndex
LazyDropWhileIterator LazyDropWhileSequence LazyFilterBidirectionalCollection LazyFilterCollection
LazyFilterIndex LazyFilterIterator LazyFilterSequence LazyMapBidirectionalCollection LazyMapCollection
LazyMapIterator LazyMapRandomAccessCollection LazyMapSequence LazyPrefixWhileBidirectionalCollection
LazyPrefixWhileCollection LazyPrefixWhileIndex LazyPrefixWhileIterator LazyPrefixWhileSequence
LazyRandomAccessCollection LazySequence LazySequenceProtocol LosslessStringConvertible MainActor
ManagedBuffer ManagedBufferPointer MemoryLayout Mirror MirrorPath MutableBidirectionalSlice
MutableCollection MutableIndexable MutableRandomAccessSlice MutableRangeReplaceableBidirectionalSlice
MutableRangeReplaceableRandomAccessSlice MutableRangeReplaceableSlice MutableRawSpan MutableRef
MutableSlice MutableSpan Never NilLiteralConvertible Numeric ObjectIdentifier OpaquePointer OptionSet
Optional OutputRawSpan OutputSpan PartialAsyncTask PartialKeyPath PartialRangeFrom PartialRangeThrough
PartialRangeUpTo PlaygroundQuickLook PrefixSequence RandomAccessCollection RandomAccessIndexable
RandomAccessSlice RandomNumberGenerator Range RangeExpression RangeReplaceableBidirectionalSlice
RangeReplaceableCollection RangeReplaceableIndexable RangeReplaceableRandomAccessSlice
RangeReplaceableSlice RangeSet RawRepresentable RawSpan Ref ReferenceWritableKeyPath Regex RegexComponent
RegexRepetitionBehavior RegexSemanticLevel RegexWordBoundaryKind Repeated Result ReversedCollection
ReversedIndex ReversedRandomAccessCollection SIMD SIMD16 SIMD2 SIMD3 SIMD32 SIMD4 SIMD64 SIMD8 SIMDMask
SIMDScalar SIMDStorage Sendable SendableMetatype Sequence SerialExecutor Set SetAlgebra SetIndex
SetIterator SignedInteger SignedNumeric SingleValueDecodingContainer SingleValueEncodingContainer Slice
Span StaticBigInt StaticString StrideThrough StrideThroughIterator StrideTo StrideToIterator Strideable
String StringInterpolationConvertible StringInterpolationProtocol StringLiteralConvertible
StringLiteralType StringProtocol Substring SuspendingClock SystemRandomNumberGenerator Task TaskExecutor
TaskGroup TaskLocal TaskPriority TextOutputStream TextOutputStreamable ThrowingDiscardingTaskGroup
ThrowingTaskGroup UInt UInt128 UInt16 UInt32 UInt64 UInt8 UTF16 UTF32 UTF8 UTF8Span UnboundedRange
UnboundedRange_ UnfoldFirstSequence UnfoldSequence Unicode UnicodeCodec UnicodeDecodingResult
UnicodeScalar UnicodeScalarLiteralConvertible UnicodeScalarType UniqueArray UniqueBox
UnkeyedDecodingContainer UnkeyedEncodingContainer Unmanaged UnownedJob UnownedSerialExecutor
UnownedTaskExecutor UnsafeBufferPointer UnsafeBufferPointerIterator UnsafeConcurrentValue
UnsafeContinuation UnsafeCurrentTask UnsafeMutableBufferPointer UnsafeMutablePointer
UnsafeMutableRawBufferPointer UnsafeMutableRawBufferPointerIterator UnsafeMutableRawPointer UnsafePointer
UnsafeRawBufferPointer UnsafeRawBufferPointerIterator UnsafeRawPointer UnsafeSendable
UnsafeThrowingContinuation UnsignedInteger Void WritableKeyPath Zip2Iterator Zip2Sequence abs all any
assert assertionFailure async asyncDetached bitCast column debugPrint detach dsohandle dump error
exchange extendLifetime externalMacro extractIsolation fatalError file fileID filePath function getVaList
globalConcurrentExecutor isKnownUniquelyReferenced isolation line max min numericCast pointwiseMax
pointwiseMin precondition preconditionFailure print readLine repeatElement sequence stride swap transcode
type unsafeBitCast unsafeDowncast warning withCheckedContinuation withCheckedThrowingContinuation
withContinuation withDiscardingTaskGroup withExtendedLifetime withTaskCancellationHandler
withTaskCancellationShield withTaskExecutorPreference withTaskGroup withTaskPriorityEscalationHandler
withTemporaryAllocation withThrowingDiscardingTaskGroup withThrowingTaskGroup withUnsafeBytes
withUnsafeContinuation withUnsafeCurrentTask withUnsafeMutableBytes withUnsafeMutablePointer
withUnsafePointer withUnsafeTemporaryAllocation withUnsafeThrowingContinuation withVaList
withoutActuallyEscaping zip
""".split())


def nodes(root):
    pending=[root] if root is not None else []
    while pending:
        node=pending.pop();yield node;pending.extend(reversed(node.named_children))


def clean(node):
    return txt(node).strip('`')


def named(node,kinds):
    return next((clean(n) for n in node.children_by_field_name('name') if n.type in kinds),'')


def flags(node):
    group=next((n for n in node.named_children if n.type=='modifiers'),None)
    return {txt(n) for n in group.named_children} if group is not None else set()


def scope(root):
    values,types=set(),set();opaque=False
    for node in nodes(root):
        if node.type in ('lambda_literal','closure_expression','type_parameters','typealias_declaration'):opaque=True
        if node!=root and node.type in ('function_declaration','class_declaration','protocol_declaration'):opaque=True
        if node.type=='parameter':
            value=named(node,{'simple_identifier'})
            if value:values.add(value)
        elif node.type=='pattern':
            bound=node.children_by_field_name('bound_identifier')
            if len(bound)==1 and bound[0].type=='simple_identifier':values.add(clean(bound[0]))
            else:opaque=True
        elif node.type in ('type_parameter','typealias_declaration'):
            name=named(node,{'type_identifier','simple_identifier'})
            if name:types.add(name)
    return dict(values=sorted(values),types=sorted(types),opaque=opaque)


def tail(node):
    while node is not None and node.type in BINARY:
        right=node.child_by_field_name('rhs')
        node=right
    return node


def head(node):
    node=tail(node)
    if node is None:return '', ''
    if node.type=='simple_identifier':return clean(node),''
    if node.type=='navigation_expression':
        target=node.child_by_field_name('target');suffix=node.child_by_field_name('suffix')
        target=tail(target)
        member=suffix.child_by_field_name('suffix') if suffix is not None else None
        if target is not None and target.type=='simple_identifier' and member is not None and member.type=='simple_identifier':
            return clean(member),clean(target)
    return '', ''


def handle_call(ex,node,function_id,class_id=None):
    callee=tail(next((n for n in node.named_children if n.type not in ('comment','multiline_comment')),None))
    name,receiver=head(callee)
    arguments=next((n for n in node.named_children if n.type=='call_suffix'),None)
    values=next((n for n in arguments.named_children if n.type=='value_arguments'),None) if arguments is not None else None
    if values is not None and any(n.children_by_field_name('reference_specifier') for n in values.named_children):
        if name:ex.references.append(dict(owner=function_id,name=name,receiver=receiver,kind='swift.function_value',line=node.start_point[0]+1))
        return callee if name else None
    ex.calls.append(dict(caller=function_id,name=name or '<indirect>',recv='swift_method' if receiver else 'swift_function',
        recv_name=receiver,line=node.start_point[0]+1,column_bytes=node.start_point[1],
        nargs=len([n for n in values.named_children if n.type=='value_argument']) if values is not None else None,
        splat=0,dynamic=int(not name),end=(node.end_point[0]+1,node.end_point[1])))
    return callee if name else None


def extract(ex,root):
    module=ex.add_sym('<module>','module',None,root,sig='')
    heads=set()
    def observe(owner,kind,node,value):
        ex.observations.append(dict(owner=owner,kind='swift.'+kind,callee='',line=node.start_point[0]+1,
            value=json.dumps(value,sort_keys=True),value_kind='json'))
    unknown=root.has_error or any(n.type=='attribute' or 'macro' in n.type or txt(n)=='dynamic' for n in nodes(root))
    excluded=unknown or any(n.type in ('import_declaration','directive') for n in nodes(root))
    for node in root.named_children:
        if node.type=='import_declaration':ex.imports.append((txt(node).partition('import')[2].strip(),'',node.start_point[0]+1))
    observe(module,'file',root,dict(excluded=bool(excluded),unknown_namespace=bool(unknown)))
    observe(module,'scope',root,dict(values=[],types=[],opaque=False,member=False,nested=False))
    def reference(owner,node):
        if node.type not in ('simple_identifier','navigation_expression'):return
        if (node.start_byte,node.end_byte,node.type) in heads:return
        name,receiver=head(node)
        if name:ex.references.append(dict(owner=owner,name=name,receiver=receiver,kind='swift.function_value',line=node.start_point[0]+1))

    def visit(node,owner,member=False,nested=False):
        if node.type in ('class_declaration','protocol_declaration'):
            kind=txt(node.child_by_field_name('declaration_kind')) or 'protocol'
            source_name=txt(node.child_by_field_name('name')).strip('`')
            name=source_name if kind!='extension' else '<extension '+source_name+'@'+str(node.start_point[0]+1)+'>'
            sid=ex.add_sym(name,'interface' if kind=='protocol' else 'enum' if kind=='enum' else 'class',owner,node)
            mods=flags(node);access=next((v for v in ('private','fileprivate','internal','public','open','package') if v in mods),'internal')
            observe(sid,'symbol',node,dict(role='extension' if kind=='extension' else 'type',name=source_name,kind=kind,access=access,
                top=ex.syms[owner]['kind']=='module',generic=any(n.type in ('type_parameters','type_constraints','inheritance_specifier') for n in node.named_children)))
            body=node.child_by_field_name('body')
            if body is not None:
                for child in body.named_children:visit(child,sid,True,True)
            return
        if node.type in ('function_declaration','init_declaration','deinit_declaration','subscript_declaration'):
            name=named(node,{'simple_identifier'}) or txt(node.child_by_field_name('name')) or node.type.removesuffix('_declaration')
            mods=flags(node);access=next((v for v in ('private','fileprivate','internal','public','open','package') if v in mods),'internal')
            parameters=[n for n in node.named_children if n.type=='parameter']
            sid=ex.add_sym(name,'method' if member else 'function',owner,node,pmin=None,pmax=len(parameters))
            observe(sid,'symbol',node,dict(role='function' if node.type=='function_declaration' else 'constructor',name=name,
                access=access,static='static' in mods,top=ex.syms[owner]['kind']=='module',body=node.child_by_field_name('body') is not None))
            info=scope(node);info.update(member=member,nested=nested)
            observe(sid,'scope',node,info)
            for child in node.named_children:
                if child.type in ('function_body','call_expression','lambda_literal') or child in node.children_by_field_name('default_value'):
                    if child in node.children_by_field_name('default_value'):reference(sid,child)
                    visit(child,sid,member,True)
            return
        if node.type=='enum_entry':
            for child in node.children_by_field_name('name'):
                if child.type!='simple_identifier':continue
                name=clean(child);sid=ex.add_sym(name,'variable',owner,node)
                observe(sid,'symbol',node,dict(role='value',name=name,access='internal',static=True,top=False,body=False))
        if node.type in ('property_declaration','typealias_declaration'):
            if node.type=='typealias_declaration':names=[named(node,{'type_identifier','simple_identifier'})]
            else:names=[clean(n) for p in node.children_by_field_name('name') for n in nodes(p) if n.type=='simple_identifier' and n.parent.type=='pattern']
            for name in names:
                if not name:continue
                sid=ex.add_sym(name,'variable',owner,node)
                observe(sid,'symbol',node,dict(role='value',name=name,access='internal',static=False,top=ex.syms[owner]['kind']=='module',body=False,
                    alias=node.type=='typealias_declaration'))
            info=scope(node);info.update(member=member,nested=nested)
            if ex.syms[owner]['kind'] not in ('function','method'):observe(owner,'scope',node,info)
            for child in node.named_children:
                if child in node.children_by_field_name('value') or child in node.children_by_field_name('computed_value'):
                    if child in node.children_by_field_name('value'):reference(owner,child)
                    visit(child,owner,member,nested)
            return
        call_head=handle_call(ex,node,owner) if node.type=='call_expression' else None
        if call_head is not None:heads.add((call_head.start_byte,call_head.end_byte,call_head.type))
        # Reference nodes are actual expression values, never declaration names/labels.
        for child in node.named_children:
            value=(any(child in node.children_by_field_name(field) for field in ('value','result','lhs','rhs','element')) or node.type=='statements')
            if value and child.type in ('simple_identifier','navigation_expression') and child!=call_head:
                reference(owner,child)
            visit(child,owner,member,nested)
    for node in root.named_children:
        if node.type not in ('function_declaration','class_declaration','protocol_declaration','property_declaration','typealias_declaration','import_declaration','directive'):
            info=scope(node);info.update(member=False,nested=False);observe(module,'scope',node,info)
        visit(node,module)
    return ex


class SwiftFiles:
    def __init__(self,db):
        self.files,self.facts,self.scopes={},{},{}
        for fid,owner,kind,value in db.execute("SELECT file_id,owner_id,kind,value FROM observations WHERE kind IN ('swift.file','swift.symbol','swift.scope')"):
            fact=json.loads(value)
            if kind=='swift.file':self.files[fid]=fact
            elif kind=='swift.symbol':self.facts[owner]=fact
            else:
                previous=self.scopes.setdefault(owner,dict(values=[],types=[],opaque=False,member=False,nested=False))
                for key in ('values','types'):previous[key]=sorted(set(previous[key])|set(fact[key]))
                for key in ('opaque','member','nested'):previous[key]|=fact[key]
        self.symbols={sid:(fid,parent) for sid,fid,parent in db.execute("SELECT s.id,s.file_id,s.parent_id FROM symbols s JOIN files f ON f.id=s.file_id WHERE f.lang='swift'")}
        self.globals=defaultdict(list);self.members=defaultdict(list);self.extensions=set()
        self.unknown_namespace=any(info['unknown_namespace'] for info in self.files.values())
        self.aliases=any(fact.get('alias') for fact in self.facts.values())
        for sid,fact in self.facts.items():
            fid,parent=self.symbols[sid]
            if fact['role']=='extension':self.extensions.add(fact['name'].split('<')[0])
            elif fact['top']:self.globals[fact['name']].append(sid)
            else:self.members[parent,fact['name']].append(sid)

    def targets(self,fid,owner,name,receiver=''):
        if self.files[fid]['excluded'] or self.unknown_namespace:return []
        info=self.scopes.get(owner,{})
        if any(info.get(k) for k in ('opaque','member','nested')):return []
        if (receiver or name) in set(info.get('values',[])+info.get('types',[])):return []
        if (receiver or name) in STDLIB_NAMES:return []
        group=self.globals[receiver or name]
        if len(group)!=1:return []
        sid=group[0];fact=self.facts[sid]
        if self.symbols[sid][0]!=fid:return []
        if not receiver:
            return [sid] if fact['role']=='function' and fact['access'] in ('private','fileprivate') and fact['body'] else []
        if self.aliases or fact['role']!='type' or fact['access'] not in ('private','fileprivate') or fact['generic'] or fact['kind'] not in ('class','struct','enum') or receiver in self.extensions:return []
        members=self.members[sid,name]
        if len(members)!=1:return []
        target=members[0];method=self.facts[target]
        return [target] if method['role']=='function' and method['static'] and method['body'] and method['access']!='private' else []
